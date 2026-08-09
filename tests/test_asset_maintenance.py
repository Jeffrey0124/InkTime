import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from asset_maintenance import AssetMaintenance
from library_scanner import LibraryScanner


class AssetMaintenanceTests(unittest.TestCase):
    def test_missing_assets_can_be_archived_and_restored_without_touching_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            source = library / "memory.jpg"
            Image.new("RGB", (30, 20), "green").save(source)
            db_path = root / "photos.db"
            scanner = LibraryScanner(db_path, library)
            scanner.scan(trigger="startup")
            conn = sqlite3.connect(db_path)
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.close()
            source.unlink()
            scanner.scan(trigger="manual")

            service = AssetMaintenance(db_path, root / "previews")
            self.assertEqual(service.set_archived([photo_id], archived=True), 1)
            self.assertFalse(source.exists())
            state = service.asset_state(photo_id)
            self.assertEqual(state["file_status"], "missing")
            self.assertEqual(state["visibility_status"], "archived")

            self.assertEqual(service.set_archived([photo_id], archived=False), 1)
            self.assertEqual(service.asset_state(photo_id)["visibility_status"], "active")

    def test_file_return_does_not_unarchive_asset(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            source = library / "memory.jpg"
            Image.new("RGB", (30, 20), "green").save(source)
            db_path = root / "photos.db"
            scanner = LibraryScanner(db_path, library)
            scanner.scan(trigger="startup")
            conn = sqlite3.connect(db_path)
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.close()
            service = AssetMaintenance(db_path, root / "previews")
            service.set_archived([photo_id], archived=True)
            source.unlink()
            scanner.scan(trigger="manual")
            Image.new("RGB", (30, 20), "green").save(source)
            scanner.scan(trigger="manual")

            state = service.asset_state(photo_id)
            self.assertEqual(state["file_status"], "present")
            self.assertEqual(state["visibility_status"], "archived")

    def test_preview_cache_is_content_addressed_and_invalidated_without_ai_work(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            source = library / "memory.jpg"
            Image.new("RGB", (300, 200), "green").save(source)
            db_path = root / "photos.db"
            scanner = LibraryScanner(db_path, library)
            scanner.scan(trigger="startup")
            conn = sqlite3.connect(db_path)
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            ai_before = conn.execute("SELECT COUNT(*) FROM analysis_tasks").fetchone()[0]
            conn.close()

            service = AssetMaintenance(db_path, root / "previews")
            first = service.ensure_preview(photo_id)
            self.assertIsNotNone(first)
            self.assertEqual(service.cached_preview(photo_id), first)
            service.set_archived([photo_id], archived=True)
            self.assertIsNone(service.cached_preview(photo_id))
            self.assertEqual(service.ensure_preview(photo_id), first)
            service.set_archived([photo_id], archived=False)
            Image.new("RGB", (300, 200), "blue").save(source)
            scanner.scan(trigger="manual")
            self.assertIsNone(service.cached_preview(photo_id))
            second = service.ensure_preview(photo_id)
            self.assertNotEqual(first, second)
            self.assertFalse(first.exists())

            conn = sqlite3.connect(db_path)
            ai_after = conn.execute("SELECT COUNT(*) FROM analysis_tasks").fetchone()[0]
            conn.close()
            self.assertEqual(ai_before, ai_after)

    def test_preview_cache_is_published_atomically(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            source = library / "memory.jpg"
            Image.new("RGB", (300, 200), "green").save(source)
            db_path = root / "photos.db"
            LibraryScanner(db_path, library).scan(trigger="startup")
            conn = sqlite3.connect(db_path)
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.close()
            preview_dir = root / "previews"
            service = AssetMaintenance(db_path, preview_dir)
            saved = threading.Event()
            release = threading.Event()
            original_save = Image.Image.save

            def slow_save(image, fp, *args, **kwargs):
                result = original_save(image, fp, *args, **kwargs)
                saved.set()
                release.wait(timeout=2)
                return result

            result = []
            with patch("asset_maintenance.Image.Image.save", new=slow_save):
                worker = threading.Thread(
                    target=lambda: result.append(service.ensure_preview(photo_id))
                )
                worker.start()
                self.assertTrue(saved.wait(timeout=2))
                self.assertIsNone(service.cached_preview(photo_id))
                self.assertEqual(list(preview_dir.glob(f"{photo_id}-*.jpg")), [])
                release.set()
                worker.join(timeout=2)

            self.assertFalse(worker.is_alive())
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0].is_file())
            self.assertGreater(result[0].stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
