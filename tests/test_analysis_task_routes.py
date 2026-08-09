import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from server import create_app


class AnalysisTaskRouteTests(unittest.TestCase):
    def test_task_control_route_returns_updated_state(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(
                db_path=root / "photos.db",
                render_output_dir=root / "renders",
                auth_required=False,
                scan_root=root,
                scan_startup=False,
            )
            store = app.extensions["settings_store"]
            channel = store.save_channel(
                {
                    "name": "Primary",
                    "provider": "custom",
                    "base_url": "https://example.com/v1",
                    "credential": {"source": "none"},
                    "models": [{"model_id": "vision-a", "is_default": True}],
                }
            )
            store.save_fallback_chain(
                [{"channel_id": channel["id"], "model_id": "vision-a"}]
            )
            conn = sqlite3.connect(root / "photos.db")
            conn.execute(
                """
                INSERT INTO photos
                (path, filename, exists_on_disk, status, file_status,
                 analysis_status, visibility_status, created_at, updated_at)
                VALUES (?, 'task.jpg', 1, 'pending', 'present', 'pending', 'active', ?, ?)
                """,
                (str(root / "task.jpg"), "2026-01-01", "2026-01-01"),
            )
            conn.commit()
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.close()
            service = app.extensions["analysis_task_service"]
            task = service.create_task(
                {"task_type": "incremental", "photo_ids": [photo_id]}
            )
            client = app.test_client()

            cancelled = client.post(
                f"/api/analysis-tasks/{task['task_id']}/control", json={"action": "cancel"}
            )

            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(cancelled.get_json()["task"]["status"], "cancelled")
            invalid = client.post(
                f"/api/analysis-tasks/{task['task_id']}/control", json={"action": "pause"}
            )
            self.assertEqual(invalid.status_code, 409)
            app.extensions["scan_coordinator"].shutdown()

    def test_library_selection_preview_and_task_creation_api(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(
                db_path=root / "photos.db",
                render_output_dir=root / "renders",
                auth_required=False,
                scan_root=root,
                scan_startup=False,
            )
            store = app.extensions["settings_store"]
            channel = store.save_channel(
                {
                    "name": "Primary",
                    "provider": "custom",
                    "base_url": "https://example.com/v1",
                    "credential": {"source": "none"},
                    "models": [{"model_id": "vision-a", "is_default": True}],
                }
            )
            store.save_fallback_chain(
                [{"channel_id": channel["id"], "model_id": "vision-a"}]
            )
            conn = sqlite3.connect(root / "photos.db")
            for index in range(2):
                conn.execute(
                    """
                    INSERT INTO photos
                    (path, filename, exists_on_disk, status, file_status,
                     analysis_status, visibility_status, created_at, updated_at)
                    VALUES (?, ?, 1, 'pending', 'present', 'pending', 'active', ?, ?)
                    """,
                    (str(root / f"{index}.jpg"), f"{index}.jpg", "2026-01-01", "2026-01-01"),
                )
            conn.commit()
            conn.close()
            client = app.test_client()

            preview = client.post(
                "/api/library/selection-preview",
                json={
                    "task_type": "incremental",
                    "selection": {"kind": "top_n", "limit": 1, "filters": {}, "sort": "filename"},
                },
            )
            self.assertEqual(preview.status_code, 200)
            selection = preview.get_json()["selection"]
            self.assertEqual(selection["selected_count"], 1)

            invalid = client.post(
                "/api/library/selection-preview",
                json={"selection": {"kind": "manual", "photo_ids": ["bad"]}},
            )
            self.assertEqual(invalid.status_code, 400)

            created = client.post(
                "/api/analysis-tasks",
                json={
                    "task_type": "incremental",
                    "photo_ids": selection["photo_ids"],
                    "concurrency": 1,
                },
            )
            self.assertEqual(created.status_code, 201)
            task = created.get_json()["task"]
            self.assertEqual(task["total_count"], 1)
            detail = client.get(f"/analysis-tasks/{task['task_id']}")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("任务已进入队列", detail.get_data(as_text=True))
            self.assertIn("data-task-controls", detail.get_data(as_text=True))

            html = client.get("/library").get_data(as_text=True)
            self.assertIn("data-library-selection", html)
            self.assertIn("data-analysis-task-dialog", html)
            source = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")
            self.assertIn("sessionStorage", source)
            self.assertIn("selection-preview", source)
            app.extensions["scan_coordinator"].shutdown()

    def test_task_creation_routes_require_admin(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = create_app(
                db_path=root / "photos.db",
                render_output_dir=root / "renders",
                auth_required=True,
                initial_admin_password="initial-pass",
                session_secret="test-secret",
                scan_root=root,
                scan_startup=False,
            )
            client = app.test_client()
            self.assertEqual(client.post("/api/library/selection-preview", json={}).status_code, 401)
            self.assertEqual(client.post("/api/analysis-tasks", json={}).status_code, 401)
            self.assertEqual(
                client.post("/api/analysis-tasks/1/control", json={"action": "cancel"}).status_code,
                401,
            )
            self.assertEqual(
                client.post("/api/analysis-tasks/1/reorder", json={"queue_position": 1}).status_code,
                401,
            )
            self.assertEqual(client.post("/api/analysis-tasks/1/retry").status_code, 401)
            app.extensions["scan_coordinator"].shutdown()


if __name__ == "__main__":
    unittest.main()
