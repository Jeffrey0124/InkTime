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
                    exif_city TEXT,
                    location_hint TEXT,
                    analysis_channel TEXT,
                    analysis_model TEXT,
                    crop_focus_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason, exif_json, side_caption, exif_city, location_hint, analysis_channel, analysis_model, crop_focus_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "KID Park",
                    "local_lmstudio",
                    "google/gemma-4-31b-qat:2",
                    '{"x": 0.2, "y": 0.3, "w": 0.4, "h": 0.5, "reason": "主体"}',
                ),
            )
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason, exif_json, side_caption, exif_city, location_hint, analysis_channel, analysis_model, crop_focus_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "",
                    "cloud_qwen",
                    "qwen3-vl-plus",
                    "",
                ),
            )
            conn.commit()
            conn.close()

            manifest = render_from_database(
                db_path=db_path,
                output_dir=output_dir,
                limit=2,
                width=800,
                height=432,
                final_height=480,
                caption_height=48,
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
            self.assertEqual(saved_manifest["height"], 432)
            self.assertEqual(saved_manifest["final_width"], 800)
            self.assertEqual(saved_manifest["final_height"], 480)
            self.assertEqual(saved_manifest["caption_height"], 48)
            self.assertEqual(saved_manifest["renders"][0]["side_caption"], "A warm red memory")
            self.assertEqual(saved_manifest["renders"][0]["analysis_channel"], "local_lmstudio")
            self.assertEqual(saved_manifest["renders"][0]["crop_focus"]["reason"], "主体")
            self.assertEqual(saved_manifest["renders"][0]["location_hint"], "Shenzhen")


if __name__ == "__main__":
    unittest.main()
