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
    def test_renders_page_serves_manifest_entries_and_render_images(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            render_dir = root / "renders"
            render_dir.mkdir()
            Image.new("RGB", (800, 480), (255, 255, 255)).save(render_dir / "render_000.png")
            (render_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "width": 800,
                        "height": 480,
                        "dither": "atkinson",
                        "renders": [
                            {
                                "source_path": str(root / "source.png"),
                                "render_png": "render_000.png",
                                "caption": "一段有用的画面描述",
                                "side_caption": "一段简短回忆",
                                "memory_score": 88.0,
                                "beauty_score": 76.0,
                                "exif_date": "2024-01-02",
                                "exif_city": "Shenzhen",
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
                    exif_city TEXT
                )
                """
            )
            conn.commit()
            conn.close()

            fake_config = types.ModuleType("config")
            fake_config.DB_PATH = str(db_path)
            fake_config.RENDER_OUTPUT_DIR = str(render_dir)
            fake_config.FLASK_HOST = "127.0.0.1"
            fake_config.FLASK_PORT = 8765
            fake_config.ENABLE_REVIEW_WEBUI = True
            sys.modules["config"] = fake_config
            sys.modules.pop("server", None)
            server = importlib.import_module("server")

            app = server.create_app(db_path=db_path, render_output_dir=render_dir)
            client = app.test_client()

            home = client.get("/")
            self.assertEqual(home.status_code, 302)
            self.assertEqual(home.headers["Location"], "/renders")

            renders = client.get("/renders")
            self.assertEqual(renders.status_code, 200)
            html = renders.get_data(as_text=True)
            self.assertIn("PhotoPainter 渲染成品", html)
            self.assertIn("本地渲染画廊", html)
            self.assertIn("一段简短回忆", html)
            self.assertIn("render_000.png", html)
            self.assertIn('href="/renders/0"', html)

            detail = client.get("/renders/0")
            self.assertEqual(detail.status_code, 200)
            detail_html = detail.get_data(as_text=True)
            self.assertIn("渲染效果预览", detail_html)
            self.assertIn("评分理由", detail_html)
            self.assertIn("更多信息", detail_html)
            self.assertIn("一段有用的画面描述", detail_html)

            image = client.get("/static/renders/render_000.png")
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.mimetype, "image/png")
            image.close()


if __name__ == "__main__":
    unittest.main()
