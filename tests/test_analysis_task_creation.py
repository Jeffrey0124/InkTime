import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis_tasks import AnalysisTaskError, AnalysisTaskService
from photo_identity import ensure_photo_identity_schema
from settings_store import SettingsStore


class AnalysisTaskCreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "photos.db"
        ensure_photo_identity_schema(self.db_path)
        self.store = SettingsStore(self.db_path)
        channel = self.store.save_channel(
            {
                "name": "Primary",
                "provider": "custom",
                "base_url": "https://example.com/v1",
                "credential": {"source": "none"},
                "models": [
                    {"model_id": "vision-a", "name": "Vision A", "is_default": True}
                ],
            }
        )
        self.store.save_fallback_chain(
            [{"channel_id": channel["id"], "model_id": "vision-a"}]
        )
        self.store.save_analysis_defaults(
            {"concurrency": 1, "high_cost_threshold": 2, "max_request_rounds": 2}
        )
        self._insert_photos()
        self.service = AnalysisTaskService(self.db_path, self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def _insert_photos(self):
        rows = [
            ("a.jpg", "pending", "present", 1, "active", "2026-01-01"),
            ("b.jpg", "pending", "present", 1, "active", "2026-01-02"),
            ("c.jpg", "pending", "present", 1, "active", "2026-01-03"),
            ("done.jpg", "analyzed", "present", 1, "active", "2026-01-04"),
            ("missing.jpg", "pending", "missing", 0, "active", "2026-01-05"),
            ("hidden.jpg", "pending", "present", 1, "archived", "2026-01-06"),
        ]
        conn = sqlite3.connect(self.db_path)
        for index, row in enumerate(rows, 1):
            filename, analysis, file_status, exists, visibility, captured_at = row
            conn.execute(
                """
                INSERT INTO photos
                (path, filename, relative_directory, file_extension, media_type,
                 size_bytes, exists_on_disk, status, file_status, analysis_status,
                 visibility_status, captured_at, created_at, updated_at)
                VALUES (?, ?, '', '.jpg', 'jpeg', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(self.root / filename),
                    filename,
                    index * 100,
                    exists,
                    analysis,
                    file_status,
                    analysis,
                    visibility,
                    captured_at,
                    f"2026-02-{index:02d}",
                    f"2026-02-{index:02d}",
                ),
            )
        done_id = conn.execute(
            "SELECT id FROM photos WHERE filename='done.jpg'"
        ).fetchone()[0]
        version_id = conn.execute(
            """
            INSERT INTO analysis_versions
            (photo_id, version_number, caption, created_at)
            VALUES (?, 1, 'done', '2026-02-04')
            """,
            (done_id,),
        ).lastrowid
        conn.execute(
            "UPDATE photos SET current_analysis_version_id=?, analysis_status='analyzed' WHERE id=?",
            (version_id, done_id),
        )
        conn.commit()
        conn.close()

    def test_preview_reports_counts_and_freezes_random_top_n(self):
        payload = {
            "task_type": "incremental",
            "selection": {
                "kind": "top_n",
                "limit": 2,
                "filters": {"file_type": "jpg"},
                "sort": "random",
                "order": "asc",
                "seed": "stable-seed",
            },
        }

        first = self.service.preview_selection(payload)
        second = self.service.preview_selection(payload)

        self.assertEqual(first["matched_count"], 6)
        self.assertEqual(first["eligible_count"], 3)
        self.assertEqual(first["selected_count"], 2)
        self.assertEqual(first["excluded_count"], 3)
        self.assertEqual(
            first["excluded_reasons"],
            {"already_analyzed": 1, "file_unavailable": 1, "not_active": 1},
        )
        self.assertEqual(first["photo_ids"], second["photo_ids"])
        self.assertEqual(first["seed"], "stable-seed")

    def test_creation_freezes_membership_strategy_and_prevents_double_occupation(self):
        preview = self.service.preview_selection(
            {
                "task_type": "incremental",
                "selection": {
                    "kind": "all",
                    "filters": {"filename": ".jpg"},
                    "sort": "filename",
                    "order": "asc",
                },
            }
        )
        created = self.service.create_task(
            {
                "task_type": "incremental",
                "name": "",
                "photo_ids": preview["photo_ids"],
                "concurrency": 2,
                "confirmed_high_cost": True,
            }
        )

        self.assertEqual(created["status"], "queued")
        self.assertEqual(created["total_count"], 3)
        self.assertTrue(created["name"].startswith("增量分析"))
        conn = sqlite3.connect(self.db_path)
        task = conn.execute(
            "SELECT model_strategy_json, concurrency FROM analysis_tasks WHERE id=?",
            (created["task_id"],),
        ).fetchone()
        strategy = json.loads(task[0])
        self.assertEqual(task[1], 2)
        self.assertEqual(strategy["max_request_rounds"], 2)
        self.assertEqual(strategy["execution_levels"][0]["model_id"], "vision-a")
        self.assertEqual(strategy["execution_levels"][0]["channel_version"], 1)
        positions = conn.execute(
            "SELECT photo_id, position FROM analysis_task_items WHERE task_id=? ORDER BY position",
            (created["task_id"],),
        ).fetchall()
        occupied = conn.execute(
            "SELECT photo_id FROM analysis_task_occupancy ORDER BY photo_id"
        ).fetchall()
        conn.close()
        self.assertEqual([row[1] for row in positions], [0, 1, 2])
        self.assertEqual([row[0] for row in positions], [row[0] for row in occupied])

        with self.assertRaisesRegex(AnalysisTaskError, "素材选择已变化"):
            self.service.create_task(
                {
                    "task_type": "incremental",
                    "photo_ids": preview["photo_ids"],
                    "concurrency": 1,
                    "confirmed_high_cost": True,
                }
            )

    def test_reanalysis_and_large_tasks_require_explicit_confirmation(self):
        incremental = self.service.preview_selection(
            {
                "task_type": "incremental",
                "selection": {"kind": "all", "filters": {}, "sort": "filename"},
            }
        )
        self.assertTrue(incremental["requires_high_cost_confirmation"])
        with self.assertRaisesRegex(AnalysisTaskError, "需要二次确认"):
            self.service.create_task(
                {
                    "task_type": "incremental",
                    "photo_ids": incremental["photo_ids"],
                    "concurrency": 1,
                }
            )

        reanalysis = self.service.preview_selection(
            {
                "task_type": "reanalysis",
                "selection": {"kind": "all", "filters": {}, "sort": "filename"},
            }
        )
        self.assertEqual(reanalysis["selected_count"], 1)
        self.assertTrue(reanalysis["requires_high_cost_confirmation"])


if __name__ == "__main__":
    unittest.main()
