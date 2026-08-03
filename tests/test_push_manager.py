import datetime as dt
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from photopainter_renderer import SIX_COLOR_PALETTE
from push_manager import (
    PushSettings,
    build_push_image,
    ensure_push_schema,
    publish_render,
    publish_scheduled,
    select_daily_photo,
    verify_six_color,
)


SHANGHAI_TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")


def _create_photo_scores(conn: sqlite3.Connection) -> None:
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


def _insert_photo(
    conn: sqlite3.Connection,
    path: Path,
    *,
    memory_score: float,
    beauty_score: float,
    exif_date: str,
    side_caption: str = "A short caption",
) -> None:
    exif_json = json.dumps({"datetime": exif_date}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO photo_scores
        (path, caption, type, memory_score, beauty_score, reason, exif_json,
         side_caption, exif_city, location_hint, analysis_channel, analysis_model, crop_focus_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(path),
            "A full caption",
            "daily",
            memory_score,
            beauty_score,
            "Good memory.",
            exif_json,
            side_caption,
            "Shanghai",
            "",
            "local_lmstudio",
            "google/gemma-4-31b-qat:2",
            '{"x": 0.2, "y": 0.2, "w": 0.5, "h": 0.5}',
        ),
    )


def _settings(root: Path) -> PushSettings:
    return PushSettings(
        db_path=root / "photos.db",
        render_output_dir=root / "renders",
        push_output_dir=root / "push",
        width=800,
        image_height=432,
        final_height=480,
        caption_height=48,
        mode="scale",
        dither="none",
        timezone="Asia/Shanghai",
        exclude_days=90,
    )


class PushManagerTests(unittest.TestCase):
    def test_render_override_defaults_do_not_replace_saved_false_values(self):
        from push_manager import normalize_render_overrides

        normalized = normalize_render_overrides(
            {"show_caption": False, "show_date": False, "show_location": False}
        )

        self.assertFalse(normalized["show_caption"])
        self.assertFalse(normalized["show_date"])
        self.assertFalse(normalized["show_location"])

    def test_photo_rotation_does_not_change_landscape_frame(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (640, 480), (220, 80, 40)).save(source)
            settings = _settings(root)
            item = {
                "source_path": str(source),
                "manual_crop_json": json.dumps(
                    {"scale": 1, "offset_x": 0, "offset_y": 0, "rotation": 90, "fit_mode": "fill"}
                ),
                "render_overrides_json": json.dumps(
                    {"dither_enabled": False, "show_caption": False, "show_date": False, "show_location": False}
                ),
            }

            rendered = build_push_image(item, settings)

            self.assertEqual(rendered.size, (800, 480))
            self.assertEqual(rendered.getpixel((400, 432)), (255, 255, 255))

    def test_portrait_frame_uses_double_height_bottom_caption_bar(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.png"
            Image.new("RGB", (640, 480), (220, 80, 40)).save(source)
            settings = _settings(root)
            item = {
                "source_path": str(source),
                "side_caption": "A short caption",
                "exif_city": "Shanghai",
                "exif_date": "2026-08-03",
                "manual_crop_json": json.dumps(
                    {"scale": 1, "offset_x": 0, "offset_y": 0, "rotation": 90, "fit_mode": "fill"}
                ),
                "render_overrides_json": json.dumps(
                    {"frame_orientation": "portrait", "dither_enabled": True}
                ),
            }

            rendered = build_push_image(item, settings)

            self.assertEqual(rendered.size, (480, 800))
            self.assertLessEqual(set(rendered.getdata()), set(SIX_COLOR_PALETTE))
            caption_pixels = list(rendered.crop((0, 704, 480, 800)).getdata())
            self.assertIn((0, 0, 0), caption_pixels)

    def test_manual_publish_writes_bmp_png_manifest_and_history(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root)
            settings.render_output_dir.mkdir()
            source = root / "source.png"
            Image.new("RGB", (320, 240), (220, 80, 40)).save(source)

            conn = sqlite3.connect(settings.db_path)
            _create_photo_scores(conn)
            _insert_photo(conn, source, memory_score=90, beauty_score=80, exif_date="2024:07:13 08:00:00")
            conn.commit()
            conn.close()

            (settings.render_output_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "width": 800,
                        "height": 432,
                        "renders": [
                            {
                                "source_path": str(source),
                                "render_png": "render_000.png",
                                "side_caption": "A short caption",
                                "caption": "A full caption",
                                "memory_score": 90,
                                "beauty_score": 80,
                                "reason": "Good memory.",
                                "exif_date": "2024-07-13",
                                "exif_city": "Shanghai",
                                "crop_focus": {"x": 0.2, "y": 0.2, "w": 0.5, "h": 0.5},
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            ensure_push_schema(settings.db_path)
            ensure_push_schema(settings.db_path)
            manifest = publish_render(
                0,
                settings=settings,
                trigger_type="manual",
                now=dt.datetime(2026, 7, 13, 7, 0, tzinfo=SHANGHAI_TZ),
            )

            bmp_path = settings.push_output_dir / "latest.bmp"
            png_path = settings.push_output_dir / "latest.png"
            manifest_path = settings.push_output_dir / "manifest.json"
            self.assertTrue(bmp_path.exists())
            self.assertTrue(png_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertEqual(manifest["image_url"], "/push/latest.bmp")
            self.assertEqual(manifest["preview_url"], "/push/latest.png")
            self.assertEqual(manifest["format"], "bmp24")
            self.assertEqual(manifest["render_width"], 800)
            self.assertEqual(manifest["render_height"], 480)
            self.assertEqual(manifest["image_height"], 432)
            self.assertEqual(manifest["caption_height"], 48)
            self.assertEqual(manifest["trigger_type"], "manual")

            with Image.open(bmp_path) as bmp:
                self.assertEqual(bmp.size, (800, 480))
                self.assertEqual(bmp.convert("RGB").mode, "RGB")
                self.assertLessEqual(set(bmp.convert("RGB").getdata()), set(SIX_COLOR_PALETTE))
            self.assertTrue(verify_six_color(bmp_path))

            saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["image_url"], "/push/latest.bmp")

            conn = sqlite3.connect(settings.db_path)
            count = conn.execute("SELECT COUNT(*) FROM push_history").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)

    def test_daily_selection_excludes_recent_pushes_then_falls_back(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root)
            settings.render_output_dir.mkdir()
            source_recent = root / "recent.png"
            source_fresh = root / "fresh.png"
            Image.new("RGB", (320, 240), (220, 80, 40)).save(source_recent)
            Image.new("RGB", (320, 240), (40, 120, 220)).save(source_fresh)

            conn = sqlite3.connect(settings.db_path)
            _create_photo_scores(conn)
            _insert_photo(conn, source_recent, memory_score=99, beauty_score=90, exif_date="2020:07:13 08:00:00")
            _insert_photo(conn, source_fresh, memory_score=80, beauty_score=90, exif_date="2020:07:14 08:00:00")
            conn.commit()
            conn.close()

            ensure_push_schema(settings.db_path)
            now = dt.datetime(2026, 7, 13, 7, 0, tzinfo=SHANGHAI_TZ)
            conn = sqlite3.connect(settings.db_path)
            conn.execute(
                """
                INSERT INTO push_history
                (source_path, render_path, pushed_at, trigger_type, slot, exif_date, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(source_recent), "old.bmp", now.isoformat(timespec="seconds"), "scheduled", "07:00", "", ""),
            )
            conn.commit()
            conn.close()

            selected = select_daily_photo(now, settings=settings)
            self.assertEqual(selected["source_path"], str(source_fresh))

            conn = sqlite3.connect(settings.db_path)
            conn.execute(
                """
                INSERT INTO push_history
                (source_path, render_path, pushed_at, trigger_type, slot, exif_date, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(source_fresh), "old.bmp", now.isoformat(timespec="seconds"), "scheduled", "07:00", "", ""),
            )
            conn.commit()
            conn.close()

            selected_after_fallback = select_daily_photo(now, settings=settings)
            self.assertEqual(selected_after_fallback["source_path"], str(source_recent))

    def test_scheduled_publish_marks_manifest_as_scheduled(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root)
            source = root / "source.png"
            Image.new("RGB", (320, 240), (220, 80, 40)).save(source)

            conn = sqlite3.connect(settings.db_path)
            _create_photo_scores(conn)
            _insert_photo(conn, source, memory_score=90, beauty_score=80, exif_date="2024:07:13 08:00:00")
            conn.commit()
            conn.close()

            manifest = publish_scheduled(
                slot="07:00",
                settings=settings,
                now=dt.datetime(2026, 7, 13, 7, 0, tzinfo=SHANGHAI_TZ),
            )
            self.assertEqual(manifest["trigger_type"], "scheduled")
            self.assertEqual(manifest["slot"], "07:00")
            self.assertEqual(manifest["image_url"], "/push/latest.bmp")

    def test_scheduled_publish_skips_candidate_that_cannot_render(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root)
            bad_source = root / "bad.png"
            good_source = root / "good.png"
            bad_source.write_text("not an image", encoding="utf-8")
            Image.new("RGB", (320, 240), (40, 120, 220)).save(good_source)

            conn = sqlite3.connect(settings.db_path)
            _create_photo_scores(conn)
            _insert_photo(conn, bad_source, memory_score=99, beauty_score=90, exif_date="2024:07:13 08:00:00")
            _insert_photo(conn, good_source, memory_score=80, beauty_score=80, exif_date="2024:07:14 08:00:00")
            conn.commit()
            conn.close()

            manifest = publish_scheduled(
                slot="07:00",
                settings=settings,
                now=dt.datetime(2026, 7, 13, 7, 0, tzinfo=SHANGHAI_TZ),
            )
            self.assertEqual(manifest["source_path"], str(good_source))
            self.assertTrue((settings.push_output_dir / "latest.bmp").exists())


if __name__ == "__main__":
    unittest.main()
