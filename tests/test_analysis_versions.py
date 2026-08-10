import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from analysis_versions import AnalysisVersionService
from photo_identity import ensure_photo_identity_schema


class AnalysisVersionServiceTests(unittest.TestCase):
    def test_compare_and_restore_keeps_immutable_history(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "photos.db"
            ensure_photo_identity_schema(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO photos(path, filename, exists_on_disk, status, file_status, analysis_status, visibility_status, created_at, updated_at) "
                "VALUES ('one.jpg', 'one.jpg', 1, 'present', 'present', 'analyzed', 'active', 'now', 'now')"
            )
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.executemany(
                "INSERT INTO analysis_versions(photo_id, version_number, caption, side_caption, photo_type, memory_score, beauty_score, reason, analysis_channel, analysis_model, result_json, created_at) "
                "VALUES (?, ?, ?, ?, '日常', ?, ?, ?, ?, ?, '{}', ?)",
                [
                    (photo_id, 1, '旧描述', '旧短文案', 70, 60, '旧理由', 'local', 'one', '2026-01-01T00:00:00+00:00'),
                    (photo_id, 2, '新描述', '新短文案', 90, 80, '新理由', 'cloud', 'two', '2026-02-01T00:00:00+00:00'),
                ],
            )
            latest_id = conn.execute("SELECT id FROM analysis_versions WHERE photo_id=? AND version_number=2", (photo_id,)).fetchone()[0]
            first_id = conn.execute("SELECT id FROM analysis_versions WHERE photo_id=? AND version_number=1", (photo_id,)).fetchone()[0]
            conn.execute("UPDATE photos SET current_analysis_version_id=? WHERE id=?", (latest_id, photo_id))
            conn.commit()
            conn.close()

            service = AnalysisVersionService(db_path)
            comparison = service.compare(photo_id, first_id, latest_id)
            self.assertEqual(comparison["caption"], {"left": "旧描述", "right": "新描述"})
            restored = service.restore(photo_id, first_id)
            self.assertEqual(restored["current_version_id"], first_id)

            conn = sqlite3.connect(db_path)
            current = conn.execute("SELECT current_analysis_version_id FROM photos WHERE id=?", (photo_id,)).fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM analysis_versions WHERE photo_id=?", (photo_id,)).fetchone()[0]
            conn.close()
            self.assertEqual(current, first_id)
            self.assertEqual(count, 2)

    def test_photo_override_survives_version_changes_and_push_draft_is_separate(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "photos.db"
            ensure_photo_identity_schema(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO photos(path, filename, exists_on_disk, status, file_status, analysis_status, visibility_status, created_at, updated_at) "
                "VALUES ('one.jpg', 'one.jpg', 1, 'present', 'present', 'analyzed', 'active', 'now', 'now')"
            )
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.execute("INSERT INTO photo_overrides(photo_id, custom_side_caption, updated_at) VALUES (?, '长期人工文案', 'now')", (photo_id,))
            conn.executemany(
                "INSERT INTO analysis_versions(photo_id, version_number, caption, side_caption, photo_type, memory_score, beauty_score, reason, result_json, created_at) VALUES (?, ?, '描述', ?, '日常', 80, 70, '理由', '{}', 'now')",
                [(photo_id, 1, '第一版 AI'), (photo_id, 2, '第二版 AI')],
            )
            first_id, second_id = [row[0] for row in conn.execute("SELECT id FROM analysis_versions WHERE photo_id=? ORDER BY version_number", (photo_id,))]
            conn.execute("UPDATE photos SET current_analysis_version_id=? WHERE id=?", (second_id, photo_id))
            conn.execute("INSERT INTO push_drafts(photo_id, caption, updated_at) VALUES (?, '仅本次推送草稿', 'now')", (photo_id,))
            conn.commit()
            conn.close()

            AnalysisVersionService(db_path).restore(photo_id, first_id)
            conn = sqlite3.connect(db_path)
            override = conn.execute("SELECT custom_side_caption FROM photo_overrides WHERE photo_id=?", (photo_id,)).fetchone()[0]
            draft = conn.execute("SELECT caption FROM push_drafts WHERE photo_id=?", (photo_id,)).fetchone()[0]
            current = conn.execute("SELECT current_analysis_version_id FROM photos WHERE id=?", (photo_id,)).fetchone()[0]
            conn.close()
            self.assertEqual(override, "长期人工文案")
            self.assertEqual(draft, "仅本次推送草稿")
            self.assertEqual(current, first_id)

    def test_admin_routes_show_versions_and_keep_push_draft_isolated(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "one.jpg"
            source.write_bytes(b"not-read-by-this-test")
            db_path = root / "photos.db"
            ensure_photo_identity_schema(db_path)
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE photo_scores (
                  path TEXT PRIMARY KEY, caption TEXT, type TEXT, memory_score REAL,
                  beauty_score REAL, reason TEXT, exif_json TEXT, side_caption TEXT,
                  exif_city TEXT, location_hint TEXT, analysis_channel TEXT,
                  analysis_model TEXT, crop_focus_json TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO photos(path, filename, exists_on_disk, status, file_status, analysis_status, visibility_status, created_at, updated_at) "
                "VALUES (?, 'one.jpg', 1, 'present', 'present', 'analyzed', 'active', 'now', 'now')",
                (str(source),),
            )
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.execute(
                "INSERT INTO photo_scores VALUES (?, '最新描述', '日常', 90, 80, '最新理由', '{}', '最新短文案', '', '', 'cloud', 'two', '')",
                (str(source),),
            )
            conn.execute(
                "INSERT INTO photo_overrides(photo_id, custom_side_caption, updated_at) VALUES (?, '长期人工文案', 'now')",
                (photo_id,),
            )
            conn.executemany(
                "INSERT INTO analysis_versions(photo_id, version_number, caption, side_caption, photo_type, memory_score, beauty_score, reason, analysis_channel, analysis_model, result_json, created_at) "
                "VALUES (?, ?, ?, ?, '日常', ?, ?, ?, ?, ?, '{}', ?)",
                [
                    (photo_id, 1, '旧描述', '旧短文案', 70, 60, '旧理由', 'local', 'one', '2026-01-01T00:00:00+00:00'),
                    (photo_id, 2, '新描述', '新短文案', 90, 80, '新理由', 'cloud', 'two', '2026-02-01T00:00:00+00:00'),
                ],
            )
            old_version, current_version = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM analysis_versions WHERE photo_id=? ORDER BY version_number",
                    (photo_id,),
                )
            ]
            conn.execute("UPDATE photos SET current_analysis_version_id=? WHERE id=?", (current_version, photo_id))
            conn.commit()
            conn.close()

            from server import create_app

            app = create_app(
                db_path=db_path,
                render_output_dir=root / "renders",
                scan_root=root,
                auth_required=False,
                analysis_worker_enabled=False,
            )
            client = app.test_client()

            detail = client.get(f"/photos/{photo_id}")
            self.assertEqual(detail.status_code, 200)
            for expected in ("旧短文案", "新短文案", "旧理由", "新理由", "70.0", "90.0"):
                self.assertIn(expected, detail.get_data(as_text=True))

            compared = client.post(
                f"/api/photos/{photo_id}/analysis-versions/compare",
                json={"left_version_id": old_version, "right_version_id": current_version},
            )
            self.assertEqual(compared.status_code, 200)
            self.assertEqual(compared.get_json()["comparison"]["caption"]["left"], "旧描述")

            restored = client.post(f"/api/photos/{photo_id}/analysis-versions/{old_version}/restore")
            self.assertEqual(restored.status_code, 200)

            saved = client.patch(
                f"/api/photos/{photo_id}/push-draft",
                json={
                    "custom_side_caption": "只属于本次推送",
                    "manual_crop_json": {"scale": 1.4},
                    "render_overrides_json": {"show_caption": False},
                },
            )
            self.assertEqual(saved.status_code, 200)
            studio = client.get(f"/push-studio/{photo_id}")
            self.assertIn("只属于本次推送", studio.get_data(as_text=True))
            self.assertIn("1.4", studio.get_data(as_text=True))

            with patch("server.write_latest_files", return_value={"ok": True}):
                pushed = client.post(f"/api/photos/{photo_id}/push")
            self.assertEqual(pushed.status_code, 200)

            conn = sqlite3.connect(db_path)
            try:
                override = conn.execute("SELECT custom_side_caption FROM photo_overrides WHERE photo_id=?", (photo_id,)).fetchone()[0]
                current = conn.execute("SELECT current_analysis_version_id FROM photos WHERE id=?", (photo_id,)).fetchone()[0]
                count = conn.execute("SELECT COUNT(*) FROM analysis_versions WHERE photo_id=?", (photo_id,)).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(override, "长期人工文案")
            self.assertEqual(current, old_version)
            self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
