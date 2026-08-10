import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from analysis_worker import AnalysisWorker
from server import create_app


class AnalysisTaskCenterTests(unittest.TestCase):
    def _app_with_task(self, root: Path):
        app = create_app(
            db_path=root / "photos.db",
            render_output_dir=root / "renders",
            auth_required=False,
            scan_root=root,
            scan_startup=False,
        )
        store = app.extensions["settings_store"]
        channel = store.save_channel({"name": "Primary", "provider": "custom", "base_url": "https://example.test/v1", "credential": {"source": "none"}, "models": [{"model_id": "vision", "is_default": True}]})
        store.save_fallback_chain([{"channel_id": channel["id"], "model_id": "vision"}])
        conn = sqlite3.connect(root / "photos.db")
        conn.execute("INSERT INTO photos(path, filename, exists_on_disk, status, file_status, analysis_status, visibility_status, created_at, updated_at) VALUES (?, 'one.jpg', 1, 'pending', 'present', 'pending', 'active', 'now', 'now')", (str(root / "one.jpg"),))
        conn.commit()
        photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
        conn.close()
        task = app.extensions["analysis_task_service"].create_task({"task_type": "incremental", "photo_ids": [photo_id]})
        return app, task

    def test_admin_reads_task_list_and_sanitized_item_snapshot(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, task = self._app_with_task(root)
            conn = sqlite3.connect(root / "photos.db")
            conn.execute("UPDATE analysis_task_items SET status='failed', started_at='2026-08-01T10:00:00+00:00', finished_at='2026-08-01T10:00:02+00:00', error_message='token=secret C:/private/photo.jpg' WHERE task_id=?", (task["task_id"],))
            conn.execute("UPDATE analysis_tasks SET failed_count=1, processed_count=1, status='completed_with_failures' WHERE id=?", (task["task_id"],))
            conn.commit()
            conn.close()

            client = app.test_client()
            listing = client.get("/api/analysis-tasks")
            detail = client.get(f"/api/analysis-tasks/{task['task_id']}/snapshot")

            self.assertEqual(listing.status_code, 200)
            self.assertEqual(listing.get_json()["tasks"][0]["task_id"], task["task_id"])
            self.assertEqual(detail.status_code, 200)
            item = detail.get_json()["items"][0]
            self.assertEqual(item["status"], "failed")
            self.assertNotIn("secret", item["error_message"])
            self.assertNotIn("C:/", item["error_message"])
            self.assertEqual(item["duration_seconds"], 2.0)
            app.extensions["scan_coordinator"].shutdown()

    def test_task_center_and_notification_endpoints_are_available(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, task = self._app_with_task(root)
            client = app.test_client()

            page = client.get("/analysis-tasks")
            self.assertEqual(page.status_code, 200)
            self.assertIn("分析任务中心", page.get_data(as_text=True))
            self.assertIn("data-task-center", page.get_data(as_text=True))
            self.assertIn("队列", page.get_data(as_text=True))

            worker = AnalysisWorker(root / "photos.db", lambda *_: {"caption": "ok", "type": "日常", "memory_score": 80, "beauty_score": 70, "reason": "ok"})
            worker.run_once()
            notices = client.get("/api/notifications")
            self.assertEqual(notices.status_code, 200)
            notification = notices.get_json()["notifications"][0]
            self.assertIn(str(task["task_id"]), notification["target_url"])
            self.assertEqual(client.post(f"/api/notifications/{notification['id']}/read").status_code, 200)
            app.extensions["scan_coordinator"].shutdown()


if __name__ == "__main__":
    unittest.main()
