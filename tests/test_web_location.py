import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from unittest.mock import patch

from web_queries import load_photo


class WebLocationTests(unittest.TestCase):
    def test_stale_monitor_mount_is_treated_as_unavailable(self):
        from web_queries import _count_monitor_files

        with patch.object(Path, "exists", side_effect=OSError(116, "Stale file handle")):
            self.assertEqual(_count_monitor_files(Path("/photos")), 0)

    def test_load_photo_hydrates_location_without_writing_during_get(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "photos.db"
            source = root / "source.jpg"
            source.write_bytes(b"placeholder")

            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE photos (
                  id INTEGER PRIMARY KEY,
                  path TEXT NOT NULL,
                  exists_on_disk INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  mtime REAL
                );
                CREATE TABLE photo_scores (
                  path TEXT PRIMARY KEY,
                  caption TEXT,
                  type TEXT,
                  memory_score REAL,
                  beauty_score REAL,
                  reason TEXT,
                  exif_json TEXT,
                  side_caption TEXT,
                  exif_city TEXT,
                  location_hint TEXT,
                  exif_gps_lat REAL,
                  exif_gps_lon REAL,
                  exif_gps_alt REAL,
                  analysis_channel TEXT,
                  analysis_model TEXT,
                  crop_focus_json TEXT
                );
                CREATE TABLE photo_overrides (
                  photo_id INTEGER PRIMARY KEY,
                  custom_side_caption TEXT,
                  manual_crop_json TEXT,
                  render_overrides_json TEXT
                );
                CREATE TABLE render_assets (
                  photo_id INTEGER,
                  preview_png_path TEXT,
                  bmp_path TEXT,
                  last_used_at TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO photos VALUES (5, ?, 1, 'analyzed', 0)",
                (str(source),),
            )
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason,
                 exif_json, side_caption, exif_city, location_hint)
                VALUES (?, 'caption', 'daily', 80, 80, 'reason', ?, 'side', '', '')
                """,
                (str(source), json.dumps({"datetime": "2026:04:05 21:12:27"})),
            )
            conn.commit()
            conn.close()

            metadata = {
                "lat": 30.8156556,
                "lon": 120.8246306,
                "alt": 8.5,
                "city": "嘉興市",
                "display": "嘉興市",
            }
            with patch("web_queries.read_location_from_source", return_value=metadata):
                photo = load_photo(db_path, 5)

            self.assertIsNotNone(photo)
            self.assertEqual(photo["exif_city"], "嘉興市")
            self.assertAlmostEqual(photo["exif_gps_lat"], 30.8156556)
            self.assertAlmostEqual(photo["exif_gps_lon"], 120.8246306)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT exif_city, location_hint, exif_gps_lat, exif_gps_lon FROM photo_scores"
            ).fetchone()
            conn.close()
            self.assertEqual(row[0], "")
            self.assertEqual(row[1], "")
            self.assertIsNone(row[2])
            self.assertIsNone(row[3])


if __name__ == "__main__":
    unittest.main()
