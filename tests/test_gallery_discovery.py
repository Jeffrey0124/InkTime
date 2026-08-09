import importlib
import json
import sqlite3
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image


@contextmanager
def gallery_client(photo_rows, push_rows=()):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        monitor_dir = root / "photos"
        monitor_dir.mkdir()
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
        paths = {}
        for row in photo_rows:
            source_path = monitor_dir / f"{row['name']}.png"
            Image.new("RGB", (64, 48), (120, 160, 200)).save(source_path)
            paths[row["name"]] = source_path
            conn.execute(
                """
                INSERT INTO photo_scores
                (path, caption, type, memory_score, beauty_score, reason, exif_json,
                 side_caption, exif_city, location_hint, analysis_channel, analysis_model,
                 crop_focus_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source_path),
                    row["name"],
                    "日常",
                    row["memory_score"],
                    row["beauty_score"],
                    "测试排序规则",
                    json.dumps({"datetime": row["exif_date"]}),
                    row["name"],
                    "苏州",
                    "",
                    "test",
                    "test-model",
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
        for name, pushed_at in push_rows:
            conn.execute(
                """
                INSERT INTO push_history
                (source_path, render_path, pushed_at, trigger_type, slot, exif_date, note)
                VALUES (?, ?, ?, 'manual', '', '', '')
                """,
                (str(paths[name]), str(root / "latest.bmp"), pushed_at),
            )
        conn.commit()
        conn.close()

        fake_config = types.ModuleType("config")
        fake_config.DB_PATH = str(db_path)
        fake_config.IMAGE_DIR = str(monitor_dir)
        fake_config.RENDER_OUTPUT_DIR = str(root / "renders")
        fake_config.PUSH_OUTPUT_DIR = str(root / "push")
        fake_config.PUSH_API_TOKEN = "secret"
        fake_config.FLASK_HOST = "127.0.0.1"
        fake_config.FLASK_PORT = 8765
        fake_config.PUSH_EXCLUDE_DAYS = 90

        original_config = sys.modules.get("config")
        sys.modules["config"] = fake_config
        sys.modules.pop("server", None)
        sys.modules.pop("push_manager", None)
        try:
            server = importlib.import_module("server")
            app = server.create_app(
                db_path=db_path,
                render_output_dir=root / "renders",
                auth_required=False,
            )
            yield app.test_client()
        finally:
            if original_config is not None:
                sys.modules["config"] = original_config
            else:
                sys.modules.pop("config", None)
            sys.modules.pop("server", None)
            sys.modules.pop("push_manager", None)


def response_names(response):
    return [Path(photo["filename"]).stem for photo in response.get_json()["photos"]]


class GalleryDiscoveryTests(unittest.TestCase):
    def test_score_and_capture_date_sorts_are_available_from_photo_api(self):
        photos = [
            {
                "name": "highest_score",
                "memory_score": 90,
                "beauty_score": 80,
                "exif_date": "2022:01:01 10:00:00",
            },
            {
                "name": "newest_date",
                "memory_score": 40,
                "beauty_score": 30,
                "exif_date": "2025:06:01 10:00:00",
            },
            {
                "name": "middle",
                "memory_score": 60,
                "beauty_score": 50,
                "exif_date": "2024:03:01 10:00:00",
            },
        ]

        with gallery_client(photos) as client:
            by_score = client.get("/api/photos?sort=score")
            by_date = client.get("/api/photos?sort=date")

        self.assertEqual(
            response_names(by_score),
            ["highest_score", "middle", "newest_date"],
        )
        self.assertEqual(
            response_names(by_date),
            ["newest_date", "middle", "highest_score"],
        )

    def test_recent_render_sort_uses_actual_push_history(self):
        photos = [
            {
                "name": "older_push",
                "memory_score": 90,
                "beauty_score": 90,
                "exif_date": "2024:01:01 10:00:00",
            },
            {
                "name": "newer_push",
                "memory_score": 60,
                "beauty_score": 60,
                "exif_date": "2023:01:01 10:00:00",
            },
        ]
        pushes = [
            ("older_push", "2026-07-01T08:00:00+08:00"),
            ("newer_push", "2026-08-01T08:00:00+08:00"),
        ]

        with gallery_client(photos, pushes) as client:
            response = client.get("/api/photos?sort=rendered")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response_names(response), ["newer_push", "older_push"])

    def test_discovery_filters_at_60_and_shuffles_within_priority_tiers(self):
        now = datetime.now().astimezone()
        anniversary = f"2020:{now.month:02d}:{now.day:02d} 10:00:00"
        ordinary_day = now.date() + timedelta(days=1)
        ordinary = f"2020:{ordinary_day.month:02d}:{ordinary_day.day:02d} 10:00:00"
        photos = [
            {
                "name": "today_a",
                "memory_score": 40,
                "beauty_score": 40,
                "exif_date": anniversary,
            },
            {
                "name": "today_b",
                "memory_score": 35,
                "beauty_score": 35,
                "exif_date": anniversary,
            },
            {
                "name": "today_c",
                "memory_score": 30,
                "beauty_score": 30,
                "exif_date": anniversary,
            },
            {
                "name": "never_a",
                "memory_score": 45,
                "beauty_score": 35,
                "exif_date": ordinary,
            },
            {
                "name": "stale_a",
                "memory_score": 50,
                "beauty_score": 20,
                "exif_date": ordinary,
            },
            {
                "name": "boundary",
                "memory_score": 30,
                "beauty_score": 30,
                "exif_date": ordinary,
            },
            {
                "name": "recent_a",
                "memory_score": 90,
                "beauty_score": 90,
                "exif_date": ordinary,
            },
            {
                "name": "recent_b",
                "memory_score": 80,
                "beauty_score": 80,
                "exif_date": ordinary,
            },
            {
                "name": "below_threshold",
                "memory_score": 30,
                "beauty_score": 29,
                "exif_date": ordinary,
            },
        ]
        pushes = [
            ("stale_a", "2020-01-01T08:00:00+08:00"),
            ("recent_a", now.isoformat(timespec="seconds")),
            ("recent_b", now.isoformat(timespec="seconds")),
        ]

        with gallery_client(photos, pushes) as client:
            alpha = client.get("/api/photos?sort=discovery&seed=alpha&limit=20")
            alpha_again = client.get("/api/photos?sort=discovery&seed=alpha&limit=20")
            beta = client.get("/api/photos?sort=discovery&seed=beta&limit=20")
            gallery = client.get("/gallery?sort=discovery&limit=20")

        alpha_names = response_names(alpha)
        self.assertEqual(alpha.status_code, 200)
        self.assertNotIn("below_threshold", alpha_names)
        self.assertIn("boundary", alpha_names)
        self.assertEqual(set(alpha_names[:3]), {"today_a", "today_b", "today_c"})
        self.assertEqual(set(alpha_names[3:6]), {"never_a", "stale_a", "boundary"})
        self.assertEqual(set(alpha_names[6:]), {"recent_a", "recent_b"})
        self.assertEqual(response_names(alpha_again), alpha_names)
        self.assertNotEqual(response_names(beta), alpha_names)
        gallery_html = gallery.get_data(as_text=True)
        self.assertIn("推送选片规则", gallery_html)
        self.assertIn("刷新候选", gallery_html)
        self.assertNotIn("随机阈值", gallery_html)
        self.assertNotIn('name="threshold"', gallery_html)


if __name__ == "__main__":
    unittest.main()
