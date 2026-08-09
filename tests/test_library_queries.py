import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from library_scanner import LibraryScanner
from web_queries import load_library_assets


class LibraryQueryTests(unittest.TestCase):
    def test_library_filters_sorts_and_reports_accurate_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            (library / "family").mkdir(parents=True)
            (library / "travel").mkdir()
            Image.new("RGB", (20, 10), "red").save(library / "family" / "old.jpg")
            Image.new("RGB", (30, 20), "green").save(library / "travel" / "new.png")
            (library / "broken.webp").write_bytes(b"broken")
            db_path = root / "photos.db"
            LibraryScanner(db_path, library).scan(trigger="manual")

            conn = sqlite3.connect(db_path)
            old_id = conn.execute(
                "SELECT id FROM photos WHERE filename='old.jpg'"
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO analysis_versions
                (photo_id, version_number, photo_type, caption, created_at)
                VALUES (?, 1, '人物', 'family', '2026-01-01')
                """,
                (old_id,),
            )
            version_id = conn.execute(
                "SELECT id FROM analysis_versions WHERE photo_id=?", (old_id,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE photos SET analysis_status='analyzed', current_analysis_version_id=?, "
                "captured_at='2020-05-02T10:00:00', gps_lat=22.5, gps_lon=114.0, "
                "created_at='2026-01-01T00:00:00+00:00' WHERE id=?",
                (version_id, old_id),
            )
            conn.execute(
                "UPDATE photos SET captured_at='2025-06-03T10:00:00', "
                "created_at='2026-02-01T00:00:00+00:00' WHERE filename='new.png'"
            )
            conn.execute(
                "UPDATE photos SET created_at='2025-01-01T00:00:00+00:00' "
                "WHERE filename='broken.webp'"
            )
            conn.commit()
            conn.close()

            default = load_library_assets(db_path)
            self.assertEqual(default["summary"]["total"], 3)
            self.assertEqual(default["summary"]["analyzable"], 2)
            self.assertEqual(default["summary"]["file_status"], {"present": 2, "unreadable": 1})
            self.assertEqual(default["summary"]["analysis_status"], {"analyzed": 1, "pending": 2})
            self.assertEqual([item["filename"] for item in default["items"]], ["new.png", "old.jpg", "broken.webp"])

            filtered = load_library_assets(
                db_path,
                analysis_status="analyzed",
                has_gps=True,
                photo_type="人物",
                directory="family",
                filename="old",
                captured_from="2020-01-01",
                captured_to="2020-12-31",
            )
            self.assertEqual(filtered["filtered_total"], 1)
            self.assertEqual(filtered["items"][0]["filename"], "old.jpg")
            self.assertEqual(filtered["items"][0]["type"], "人物")
            self.assertNotIn(str(root), str(filtered))

            file_type = load_library_assets(db_path, file_type=".png")
            self.assertEqual([item["filename"] for item in file_type["items"]], ["new.png"])
            self.assertEqual(file_type["items"][0]["type"], "")
            ai_type_is_separate = load_library_assets(
                db_path, file_type=".png", photo_type="人物"
            )
            self.assertEqual(ai_type_is_separate["filtered_total"], 0)

            ascending = load_library_assets(db_path, sort="size", order="asc")
            sizes = [item["size_bytes"] for item in ascending["items"]]
            self.assertEqual(sizes, sorted(sizes))
            descending = load_library_assets(db_path, sort="filename", order="desc")
            names = [item["filename"] for item in descending["items"]]
            self.assertEqual(names, sorted(names, reverse=True))


if __name__ == "__main__":
    unittest.main()
