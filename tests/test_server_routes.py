import importlib
import json
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image


class ServerRoutesTests(unittest.TestCase):
    def test_renders_routes_and_manual_push_api(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.png"
            render_dir = root / "renders"
            push_dir = root / "push"
            render_dir.mkdir()

            Image.new("RGB", (320, 240), (240, 80, 80)).save(source_path)
            Image.new("RGB", (800, 432), (255, 255, 255)).save(render_dir / "render_000.png")
            (render_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "width": 800,
                        "height": 432,
                        "final_width": 800,
                        "final_height": 480,
                        "caption_height": 48,
                        "dither": "none",
                        "renders": [
                            {
                                "source_path": str(source_path),
                                "render_png": "render_000.png",
                                "caption": "A useful scene description",
                                "side_caption": "Short memory",
                                "type": "daily",
                                "memory_score": 88.0,
                                "beauty_score": 76.0,
                                "reason": "Good color and subject.",
                                "exif_date": "2024-01-02",
                                "exif_city": "Shenzhen",
                                "location_hint": "Do not display this",
                                "analysis_channel": "local_lmstudio",
                                "analysis_model": "google/gemma-4-31b-qat:2",
                                "crop_focus": {
                                    "x": 0.2,
                                    "y": 0.3,
                                    "w": 0.4,
                                    "h": 0.5,
                                    "reason": "subject",
                                },
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            db_path = root / "photos.db"
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
            conn.commit()
            conn.close()

            fake_config = types.ModuleType("config")
            fake_config.DB_PATH = str(db_path)
            fake_config.RENDER_OUTPUT_DIR = str(render_dir)
            fake_config.PUSH_OUTPUT_DIR = str(push_dir)
            fake_config.PUSH_API_TOKEN = "secret"
            fake_config.FLASK_HOST = "127.0.0.1"
            fake_config.FLASK_PORT = 8765
            fake_config.ENABLE_REVIEW_WEBUI = True
            fake_config.RENDER_WIDTH = 800
            fake_config.RENDER_HEIGHT = 432
            fake_config.FINAL_RENDER_HEIGHT = 480
            fake_config.CAPTION_BAR_HEIGHT = 48
            fake_config.RENDER_MODE = "scale"
            fake_config.DITHER_MODE = "none"
            fake_config.BRIGHTNESS = 1.1
            fake_config.CONTRAST = 1.2
            fake_config.SATURATION = 1.2
            fake_config.FONT_PATH = ""
            fake_config.PUSH_TIMEZONE = "Asia/Shanghai"
            fake_config.PUSH_EXCLUDE_DAYS = 90

            original_config = sys.modules.get("config")
            sys.modules["config"] = fake_config
            sys.modules.pop("server", None)
            sys.modules.pop("push_manager", None)
            try:
                server = importlib.import_module("server")

                app = server.create_app(db_path=db_path, render_output_dir=render_dir)
                client = app.test_client()

                home = client.get("/")
                self.assertEqual(home.status_code, 302)
                self.assertEqual(home.headers["Location"], "/renders")

                health = client.get("/healthz")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.get_json(), {"ok": True})
                health.close()

                renders = client.get("/renders")
                self.assertEqual(renders.status_code, 200)
                html = renders.get_data(as_text=True)
                self.assertIn("render_000.png", html)
                self.assertIn("local_lmstudio", html)
                self.assertIn('href="/renders/0"', html)

                detail = client.get("/renders/0")
                self.assertEqual(detail.status_code, 200)
                detail_html = detail.get_data(as_text=True)
                self.assertIn("Short memory", detail_html)
                self.assertIn("google/gemma-4-31b-qat:2", detail_html)
                self.assertIn("/static/location.svg", detail_html)
                self.assertIn("pushRender(0)", detail_html)
                self.assertNotIn("Do not display this", detail_html)

                unauthorized = client.post("/api/push/manual/0")
                self.assertEqual(unauthorized.status_code, 401)

                pushed = client.post("/api/push/manual/0", headers={"X-Push-Token": "secret"})
                self.assertEqual(pushed.status_code, 200)
                payload = pushed.get_json()
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["image_url"], "/push/latest.bmp")
                self.assertEqual(payload["preview_url"], "/push/latest.png")
                self.assertEqual(payload["trigger_type"], "manual")

                bmp = client.get("/push/latest.bmp")
                self.assertEqual(bmp.status_code, 200)
                self.assertGreater(len(bmp.data), 100)
                bmp.close()

                png = client.get("/push/latest.png")
                self.assertEqual(png.status_code, 200)
                self.assertEqual(png.mimetype, "image/png")
                png.close()

                manifest = client.get("/push/manifest.json")
                self.assertEqual(manifest.status_code, 200)
                self.assertEqual(manifest.get_json()["image_url"], "/push/latest.bmp")
                manifest.close()

                image = client.get("/static/renders/render_000.png")
                self.assertEqual(image.status_code, 200)
                self.assertEqual(image.mimetype, "image/png")
                image.close()
            finally:
                if original_config is not None:
                    sys.modules["config"] = original_config
                else:
                    sys.modules.pop("config", None)
                sys.modules.pop("server", None)
                sys.modules.pop("push_manager", None)


if __name__ == "__main__":
    unittest.main()
