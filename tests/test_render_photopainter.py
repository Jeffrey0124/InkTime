import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from render_photopainter import render_from_database


class RenderPhotoPainterFlowTests(unittest.TestCase):
    def test_render_from_database_writes_manifest_and_latest_preview(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_a = root / "a.png"
            source_b = root / "b.png"
            db_path = root / "photos.db"
            output_dir = root / "renders"

            Image.new("RGB", (320, 240), (240, 80, 80)).save(source_a)
            Image.new("RGB", (320, 240), (80, 120, 220)).save(source_b)

            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE photo_scores (
                    path TEXT PRIMARY KEY,
                    caption TEXT,
                    type TEXT,
                    memory_score REAL,
                    beauty_score REAL,
                    reason TEXT,
                    exif_json TEXT,
                    side_caption TEXT,
                    exif_city TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason, exif_json, side_caption, exif_city)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source_a),
                    "red memory",
                    "photo",
                    91.0,
                    75.0,
                    "strong color",
                    '{"datetime": "2024:01:02 03:04:05"}',
                    "A warm red memory",
                    "Shenzhen",
                ),
            )
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason, exif_json, side_caption, exif_city)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source_b),
                    "blue memory",
                    "photo",
                    80.0,
                    82.0,
                    "cool color",
                    '{"datetime": "2023:05:06 07:08:09"}',
                    "A blue day",
                    "Guangzhou",
                ),
            )
            conn.commit()
            conn.close()

            manifest = render_from_database(
                db_path=db_path,
                output_dir=output_dir,
                limit=2,
                width=800,
                height=480,
                dither="none",
                save_bmp=False,
            )

            self.assertEqual(len(manifest["renders"]), 2)
            self.assertEqual(manifest["renders"][0]["source_path"], str(source_a))
            self.assertEqual(manifest["renders"][0]["render_png"], "render_000.png")
            self.assertTrue((output_dir / "render_000.png").exists())
            self.assertTrue((output_dir / "render_001.png").exists())
            self.assertTrue((output_dir / "latest.png").exists())

            saved_manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["width"], 800)
            self.assertEqual(saved_manifest["height"], 480)
            self.assertEqual(saved_manifest["renders"][0]["side_caption"], "A warm red memory")


if __name__ == "__main__":
    unittest.main()
