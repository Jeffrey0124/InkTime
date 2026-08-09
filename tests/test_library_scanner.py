import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
import pillow_heif

from library_scanner import LibraryScanner, ScanCoordinator
from photo_identity import ensure_photo_identity_schema


class LibraryScannerTests(unittest.TestCase):
    def test_scan_records_supported_assets_exclusions_unreadable_and_no_ai_side_effects(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            Image.new("RGB", (20, 10), "red").save(library / "one.jpg")
            Image.new("RGB", (20, 10), "red").save(library / "alias.jpeg")
            Image.new("RGB", (20, 10), "green").save(library / "two.png")
            Image.new("RGB", (20, 10), "blue").save(library / "three.webp")
            pillow_heif.from_pillow(Image.new("RGB", (18, 12), "yellow")).save(
                library / "readable.heic"
            )
            (library / "broken.heic").write_bytes(b"not-an-image")
            (library / "broken.heif").write_bytes(b"not-an-image")
            (library / "note.txt").write_text("ignore", encoding="utf-8")
            excluded = library / "@eaDir"
            excluded.mkdir()
            Image.new("RGB", (10, 10), "white").save(excluded / "hidden.jpg")
            custom = library / "private"
            custom.mkdir()
            Image.new("RGB", (10, 10), "white").save(custom / "secret.jpg")

            db_path = root / "photos.db"
            ensure_photo_identity_schema(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO analysis_tasks "
                "(name, task_type, status, created_at, updated_at) "
                "VALUES ('existing', 'analysis', 'queued', 'now', 'now')"
            )
            conn.commit()
            conn.close()

            result = LibraryScanner(
                db_path,
                library,
                exclude_patterns=["private/**"],
            ).scan(trigger="manual")

            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT path, file_status, analysis_status, file_extension, width, height "
                "FROM photos ORDER BY path"
            ).fetchall()
            ai_count = conn.execute("SELECT COUNT(*) FROM analysis_tasks").fetchone()[0]
            conn.close()

            self.assertEqual(result.discovered_count, 7)
            self.assertEqual(result.readable_count, 5)
            self.assertEqual(result.unreadable_count, 2)
            self.assertEqual(ai_count, 1)
            self.assertEqual(
                {Path(row["path"]).name for row in rows},
                {"one.jpg", "alias.jpeg", "two.png", "three.webp", "readable.heic", "broken.heic", "broken.heif"},
            )
            by_name = {Path(row["path"]).name: row for row in rows}
            self.assertEqual(by_name["broken.heic"]["file_status"], "unreadable")
            self.assertEqual(by_name["broken.heif"]["file_status"], "unreadable")
            self.assertEqual(by_name["readable.heic"]["file_status"], "present")
            self.assertEqual(
                (by_name["readable.heic"]["width"], by_name["readable.heic"]["height"]),
                (18, 12),
            )
            self.assertTrue(all(row["analysis_status"] == "pending" for row in rows))

    def test_missing_file_is_marked_without_deleting_analysis_history(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            source = library / "memory.jpg"
            Image.new("RGB", (20, 10), "red").save(source)
            db_path = root / "photos.db"
            scanner = LibraryScanner(db_path, library)
            scanner.scan(trigger="startup")

            conn = sqlite3.connect(db_path)
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.execute(
                "INSERT INTO analysis_versions "
                "(photo_id, version_number, caption, created_at) VALUES (?, 1, 'keep me', 'now')",
                (photo_id,),
            )
            conn.commit()
            conn.close()
            source.unlink()

            scanner.scan(trigger="scheduled")

            conn = sqlite3.connect(db_path)
            state = conn.execute(
                "SELECT file_status, exists_on_disk FROM photos WHERE id = ?", (photo_id,)
            ).fetchone()
            history = conn.execute("SELECT caption FROM analysis_versions").fetchall()
            conn.close()
            self.assertEqual(state, ("missing", 0))
            self.assertEqual(history, [("keep me",)])

    def test_concurrent_scan_requests_reuse_one_active_task(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            Image.new("RGB", (20, 10), "red").save(library / "one.jpg")
            entered = threading.Event()
            release = threading.Event()

            class BlockingScanner(LibraryScanner):
                def scan(self, *, trigger, task_id=None):
                    entered.set()
                    release.wait(timeout=5)
                    return super().scan(trigger=trigger, task_id=task_id)

            coordinator = ScanCoordinator(BlockingScanner(root / "photos.db", library))
            first = coordinator.start("startup")
            self.assertTrue(entered.wait(timeout=5))
            second = coordinator.start("scheduled")
            third = coordinator.start("manual")
            self.assertEqual(first.task_id, second.task_id)
            self.assertEqual(first.task_id, third.task_id)
            self.assertTrue(second.reused)
            self.assertTrue(third.reused)
            release.set()
            coordinator.wait(first.task_id, timeout=5)

            conn = sqlite3.connect(root / "photos.db")
            tasks = conn.execute("SELECT COUNT(*) FROM scan_tasks").fetchone()[0]
            triggers = conn.execute(
                "SELECT trigger_sources_json FROM scan_tasks"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(tasks, 1)
            self.assertIn("startup", triggers)
            self.assertIn("scheduled", triggers)
            self.assertIn("manual", triggers)

    def test_orphaned_active_task_is_failed_before_a_new_owned_scan_starts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            Image.new("RGB", (20, 10), "red").save(library / "one.jpg")
            scanner = LibraryScanner(root / "photos.db", library)
            conn = sqlite3.connect(root / "photos.db")
            orphan_id = conn.execute(
                """
                INSERT INTO scan_tasks
                (status, root_path, trigger_sources_json, created_at, started_at)
                VALUES ('running', ?, '["startup"]', 'old', 'old')
                """,
                (str(library),),
            ).lastrowid
            conn.commit()
            conn.close()

            coordinator = ScanCoordinator(scanner)
            started = coordinator.start("manual")
            self.assertNotEqual(started.task_id, orphan_id)
            result = coordinator.wait(started.task_id, timeout=5)
            self.assertEqual(result.discovered_count, 1)

            conn = sqlite3.connect(root / "photos.db")
            orphan = conn.execute(
                "SELECT status, error_message FROM scan_tasks WHERE id=?", (orphan_id,)
            ).fetchone()
            conn.close()
            self.assertEqual(orphan, ("failed", "interrupted_by_process_restart"))


if __name__ == "__main__":
    unittest.main()
