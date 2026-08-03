import importlib
import io
import json
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from photopainter_renderer import SIX_COLOR_PALETTE


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
                self.assertEqual(home.status_code, 200)
                home_html = home.get_data(as_text=True)
                self.assertIn("状态中控台", home_html)
                self.assertIn('data-log-toggle aria-expanded="true"', home_html)
                self.assertIn('type="button" disabled title="照片库扫描任务将在后续阶段接入"', home_html)
                self.assertNotIn('href="/api/status">重新扫描照片库', home_html)

                status = client.get("/api/status")
                self.assertEqual(status.status_code, 200)
                status_payload = status.get_json()
                self.assertTrue(status_payload["ok"])
                self.assertEqual(status_payload["analyzed_photos"], 0)
                self.assertEqual(status_payload["missing_photos"], 0)
                self.assertIsNone(status_payload["recent_push"])
                status.close()

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
                with Image.open(io.BytesIO(bmp.data)) as pushed_image:
                    pushed_image.load()
                    self.assertEqual(pushed_image.size, (800, 480))
                    self.assertEqual(pushed_image.mode, "RGB")
                    self.assertLessEqual(
                        set(pushed_image.getdata()),
                        set(SIX_COLOR_PALETTE),
                    )
                bmp.close()

                png = client.get("/push/latest.png")
                self.assertEqual(png.status_code, 200)
                self.assertEqual(png.mimetype, "image/png")
                png.close()

                manifest = client.get("/push/manifest.json")
                self.assertEqual(manifest.status_code, 200)
                self.assertEqual(manifest.get_json()["image_url"], "/push/latest.bmp")
                manifest.close()

                status_after_push = client.get("/api/status")
                self.assertEqual(status_after_push.status_code, 200)
                self.assertIsNotNone(status_after_push.get_json()["recent_push"])
                status_after_push.close()

                image = client.get("/static/renders/render_000.png")
                self.assertEqual(image.status_code, 200)
                self.assertEqual(image.mimetype, "image/png")
                image.close()

                review = client.get("/review")
                self.assertEqual(review.status_code, 200)
                self.assertIn("照片分析结果", review.get_data(as_text=True))
                review.close()

                rerender = client.get("/render/0")
                self.assertEqual(rerender.status_code, 302)
                self.assertTrue(rerender.headers["Location"].endswith("/renders/0"))
                rerender.close()
            finally:
                if original_config is not None:
                    sys.modules["config"] = original_config
                else:
                    sys.modules.pop("config", None)
                sys.modules.pop("server", None)
                sys.modules.pop("push_manager", None)

    def test_status_and_database_gallery_api(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor_dir = root / "photos"
            push_dir = root / "push"
            monitor_dir.mkdir()
            push_dir.mkdir()
            source_path = monitor_dir / "warm.png"
            missing_path = monitor_dir / "missing.png"
            db_path = root / "photos.db"

            Image.new("RGB", (320, 420), (80, 180, 120)).save(source_path)
            Image.new("RGB", (800, 480), (255, 255, 255)).save(push_dir / "latest.png")
            (push_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "image_url": "/push/latest.bmp",
                        "preview_url": "/push/latest.png",
                        "published_at": "2026-08-01T07:00:00+08:00",
                        "trigger_type": "manual",
                        "source_path": str(source_path),
                        "side_caption": "春天在笑",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

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
                (path, caption, type, memory_score, beauty_score, reason, exif_json,
                 side_caption, exif_city, location_hint, analysis_channel, analysis_model, crop_focus_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?),
                       (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source_path),
                    "孩子在春天的草地上玩",
                    "孩子/日常",
                    81,
                    72,
                    "人物清楚，色彩温暖",
                    json.dumps({"datetime": "2026:04:04 10:00:00"}, ensure_ascii=False),
                    "春天在笑",
                    "木渎镇",
                    "木渎镇",
                    "local_lmstudio",
                    "google/gemma",
                    "",
                    str(missing_path),
                    "一张已经不在磁盘上的照片",
                    "日常",
                    90,
                    88,
                    "历史记录应保留",
                    json.dumps({"datetime": "2025:01:01 08:00:00"}, ensure_ascii=False),
                    "旧照片仍留着",
                    "苏州",
                    "苏州",
                    "cloud_qwen",
                    "qwen3-vl-plus",
                    "",
                ),
            )
            conn.execute(
                """
                CREATE TABLE push_history (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source_path TEXT NOT NULL,
                  render_path TEXT NOT NULL,
                  pushed_at TEXT NOT NULL,
                  trigger_type TEXT NOT NULL,
                  slot TEXT,
                  exif_date TEXT,
                  note TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO push_history
                (source_path, render_path, pushed_at, trigger_type, slot, exif_date, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source_path),
                    str(push_dir / "latest.bmp"),
                    "2026-08-01T07:00:00+08:00",
                    "manual",
                    "",
                    "2026-04-04",
                    "",
                ),
            )
            conn.commit()
            conn.close()

            fake_config = types.ModuleType("config")
            fake_config.DB_PATH = str(db_path)
            fake_config.IMAGE_DIR = str(monitor_dir)
            fake_config.RENDER_OUTPUT_DIR = str(root / "renders")
            fake_config.PUSH_OUTPUT_DIR = str(push_dir)
            fake_config.PUSH_API_TOKEN = "secret"
            fake_config.FLASK_HOST = "127.0.0.1"
            fake_config.FLASK_PORT = 8765

            original_config = sys.modules.get("config")
            sys.modules["config"] = fake_config
            sys.modules.pop("server", None)
            sys.modules.pop("push_manager", None)
            try:
                server = importlib.import_module("server")

                app = server.create_app(db_path=db_path, render_output_dir=root / "renders")
                client = app.test_client()

                status = client.get("/api/status")
                self.assertEqual(status.status_code, 200)
                status_payload = status.get_json()
                self.assertEqual(status_payload["monitored_files"], 1)
                self.assertEqual(status_payload["analyzed_photos"], 2)
                self.assertEqual(status_payload["missing_photos"], 1)
                self.assertEqual(status_payload["recent_push"]["preview_url"], "/push/latest.png")
                status.close()

                photos = client.get("/api/photos")
                self.assertEqual(photos.status_code, 200)
                payload = photos.get_json()
                self.assertEqual(len(payload["photos"]), 1)
                photo = payload["photos"][0]
                self.assertIsInstance(photo["photo_id"], int)
                self.assertEqual(photo["side_caption"], "春天在笑")
                self.assertEqual(photo["exif_date"], "2026-04-04")
                self.assertEqual(photo["exif_city"], "木渎镇")
                self.assertEqual(photo["score"], 153.0)
                self.assertIn(f"/api/photos/{photo['photo_id']}/source", photo["source_url"])
                photos.close()

                detail_api = client.get(f"/api/photos/{photo['photo_id']}")
                self.assertEqual(detail_api.status_code, 200)
                detail_payload = detail_api.get_json()
                self.assertEqual(detail_payload["photo"]["photo_id"], photo["photo_id"])
                self.assertEqual(detail_payload["photo"]["ai_side_caption"], "春天在笑")
                detail_api.close()

                self.assertEqual(status_payload["recent_push"]["photo_id"], photo["photo_id"])

                home = client.get("/")
                self.assertEqual(home.status_code, 200)
                home_html = home.get_data(as_text=True)
                self.assertIn(f'href="/push-studio/{photo["photo_id"]}"', home_html)
                self.assertIn("调整这张图", home_html)

                override_payload = {
                    "custom_side_caption": "手掌托起一座小城",
                    "manual_crop_json": {
                        "offset_x": 18,
                        "offset_y": -9,
                        "scale": 1.35,
                        "rotation": 90,
                        "fit_mode": "fill",
                    },
                    "render_overrides_json": {
                        "show_caption": True,
                        "show_date": True,
                        "show_location": False,
                        "dither_enabled": False,
                        "dither_type": "stucki",
                        "dither_strength": 1.4,
                        "brightness": 1.1,
                        "contrast": 1.2,
                        "saturation": 1.2,
                    },
                }
                saved = client.patch(
                    f"/api/photos/{photo['photo_id']}/overrides",
                    json=override_payload,
                )
                self.assertEqual(saved.status_code, 200)
                saved_payload = saved.get_json()
                self.assertTrue(saved_payload["ok"])
                self.assertEqual(saved_payload["custom_side_caption"], "手掌托起一座小城")
                saved.close()

                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                override_row = conn.execute(
                    """
                    SELECT custom_side_caption, manual_crop_json, render_overrides_json
                    FROM photo_overrides
                    WHERE photo_id = ?
                    """,
                    (photo["photo_id"],),
                ).fetchone()
                conn.close()
                self.assertEqual(override_row["custom_side_caption"], "手掌托起一座小城")
                self.assertEqual(json.loads(override_row["manual_crop_json"])["scale"], 1.35)

                caption_only = client.patch(
                    f"/api/photos/{photo['photo_id']}/overrides",
                    json={"custom_side_caption": "只修改这一句"},
                )
                self.assertEqual(caption_only.status_code, 200)
                caption_only.close()
                conn = sqlite3.connect(db_path)
                preserved = conn.execute(
                    "SELECT manual_crop_json, render_overrides_json FROM photo_overrides WHERE photo_id = ?",
                    (photo["photo_id"],),
                ).fetchone()
                conn.close()
                self.assertEqual(json.loads(preserved[0])["scale"], 1.35)
                self.assertEqual(json.loads(preserved[1])["dither_type"], "stucki")

                studio = client.get(f"/push-studio/{photo['photo_id']}")
                self.assertEqual(studio.status_code, 200)
                studio_html = studio.get_data(as_text=True)
                self.assertIn('data-push-studio', studio_html)
                self.assertIn('data-save-url=', studio_html)
                self.assertIn('data-push-url=', studio_html)
                self.assertIn('data-editor-canvas', studio_html)
                self.assertIn("只修改这一句", studio_html)
                self.assertIn("Jarvis-Judice-Ninke", studio_html)
                self.assertIn("PhotoPainter Atkinson（推荐）", studio_html)
                self.assertIn("Atkinson（标准）", studio_html)
                self.assertIn("自动配置", studio_html)
                self.assertIn('data-frame-orientation="landscape"', studio_html)
                self.assertIn('data-frame-orientation="portrait"', studio_html)
                self.assertIn("照片旋转 90°", studio_html)
                self.assertIn("保存参数", studio_html)
                self.assertIn('data-display-defaults-version="2"', studio_html)

                detail = client.get(f"/photos/{photo['photo_id']}")
                detail_html = detail.get_data(as_text=True)
                self.assertIn("AI 原始短文案", detail_html)
                self.assertIn("人工文案", detail_html)
                self.assertIn("只修改这一句", detail_html)
                detail.close()

                unauthorized = client.post(f"/api/photos/{photo['photo_id']}/push")
                self.assertEqual(unauthorized.status_code, 401)
                unauthorized.close()

                pushed = client.post(
                    f"/api/photos/{photo['photo_id']}/push",
                    headers={"X-Push-Token": "secret"},
                )
                self.assertEqual(pushed.status_code, 200)
                pushed_payload = pushed.get_json()
                self.assertTrue(pushed_payload["ok"])
                self.assertEqual(pushed_payload["manifest"]["manual_crop"]["rotation"], 90)
                self.assertEqual(pushed_payload["manifest"]["render_width"], 800)
                self.assertEqual(pushed_payload["manifest"]["render_height"], 480)
                self.assertEqual(
                    pushed_payload["manifest"]["render_overrides"]["dither_type"],
                    "stucki",
                )
                self.assertTrue((push_dir / "latest.bmp").exists())
                self.assertTrue((push_dir / "latest.png").exists())
                pushed.close()

                with_missing = client.get("/api/photos?include_missing=1")
                self.assertEqual(with_missing.status_code, 200)
                self.assertEqual(len(with_missing.get_json()["photos"]), 2)
                with_missing.close()

                gallery = client.get("/gallery")
                self.assertEqual(gallery.status_code, 200)
                gallery_html = gallery.get_data(as_text=True)
                self.assertIn("已分析照片瀑布流", gallery_html)
                self.assertIn("只修改这一句", gallery_html)
                self.assertIn("加入推送", gallery_html)
                self.assertIn(f"#photo-{photo['photo_id']}", gallery_html)
                self.assertIn(f"/push-studio/{photo['photo_id']}", gallery_html)
                self.assertNotIn("查看详情", gallery_html)

                source = client.get(f"/api/photos/{photo['photo_id']}/source")
                self.assertEqual(source.status_code, 200)
                self.assertEqual(source.mimetype, "image/png")
                source.close()
            finally:
                if original_config is not None:
                    sys.modules["config"] = original_config
                else:
                    sys.modules.pop("config", None)
                sys.modules.pop("server", None)
                sys.modules.pop("push_manager", None)


if __name__ == "__main__":
    unittest.main()
