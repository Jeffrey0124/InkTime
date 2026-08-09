import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from server import create_app


class LibraryRouteTests(unittest.TestCase):
    def test_empty_library_page_displays_zero_counts(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            app = create_app(
                db_path=root / "photos.db",
                render_output_dir=root / "renders",
                auth_required=False,
                scan_root=library,
                scan_startup=False,
            )

            html = app.test_client().get("/library").get_data(as_text=True)
            self.assertEqual(html.count("<strong>0</strong>"), 3)
            app.extensions["scan_coordinator"].shutdown()

    def test_admin_library_page_scan_api_filters_and_auth_boundary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            Image.new("RGB", (20, 10), "red").save(library / "new.jpg")
            app = create_app(
                db_path=root / "photos.db",
                render_output_dir=root / "renders",
                auth_required=False,
                scan_root=library,
                scan_startup=False,
            )
            client = app.test_client()

            started = client.post("/api/library/scan")
            self.assertEqual(started.status_code, 202)
            task_id = started.get_json()["task_id"]
            app.extensions["scan_coordinator"].wait(task_id, timeout=5)

            response = client.get(
                "/api/library?sort=filename&order=asc&analysis_status=pending&file_type=.jpg"
            )
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertEqual(payload["summary"]["total"], 1)
            self.assertEqual(payload["summary"]["analyzable"], 1)
            self.assertEqual(payload["filtered_total"], 1)
            self.assertEqual(payload["items"][0]["filename"], "new.jpg")
            self.assertNotIn(str(root), response.get_data(as_text=True))

            page = client.get("/library")
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True)
            self.assertIn("素材库", html)
            self.assertIn("new.jpg", html)
            self.assertIn('href="/library"', html)
            self.assertIn("文件状态", html)
            self.assertIn('name="file_type"', html)

            conn = sqlite3.connect(root / "photos.db")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM analysis_tasks").fetchone()[0], 0)
            conn.close()

            protected = create_app(
                db_path=root / "protected.db",
                render_output_dir=root / "renders-2",
                auth_required=True,
                initial_admin_password="initial-pass",
                session_secret="stable-test-secret",
                scan_root=library,
                scan_startup=False,
            ).test_client()
            self.assertEqual(protected.get("/library").status_code, 302)
            self.assertEqual(protected.get("/api/library").status_code, 401)
            self.assertEqual(protected.post("/api/library/scan").status_code, 401)

    def test_explicit_startup_scan_begins_without_an_http_request(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            Image.new("RGB", (20, 10), "red").save(library / "startup.jpg")
            db_path = root / "photos.db"

            app = create_app(
                db_path=db_path,
                render_output_dir=root / "renders",
                auth_required=False,
                scan_root=library,
                scan_startup=True,
            )

            conn = sqlite3.connect(db_path)
            task_id = conn.execute(
                "SELECT id FROM scan_tasks ORDER BY id DESC LIMIT 1"
            ).fetchone()[0]
            conn.close()
            result = app.extensions["scan_coordinator"].wait(task_id, timeout=5)
            self.assertEqual(result.discovered_count, 1)
            app.extensions["scan_coordinator"].shutdown()

    def test_explicit_scan_schedule_starts_without_an_http_request(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            app = create_app(
                db_path=root / "photos.db",
                render_output_dir=root / "renders",
                auth_required=False,
                scan_root=library,
                scan_startup=False,
                scan_interval_minutes=60,
            )

            self.assertIn("scan_scheduler", app.extensions)
            self.assertTrue(app.extensions["scan_scheduler"].running)
            app.extensions["scan_scheduler"].shutdown(wait=True)
            app.extensions["scan_coordinator"].shutdown()

    def test_admin_can_batch_archive_missing_assets_and_restore_visibility(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "library"
            library.mkdir()
            source = library / "missing.jpg"
            Image.new("RGB", (20, 10), "red").save(source)
            db_path = root / "photos.db"
            app = create_app(
                db_path=db_path,
                render_output_dir=root / "renders",
                auth_required=False,
                scan_root=library,
                scan_startup=False,
            )
            client = app.test_client()
            task_id = client.post("/api/library/scan").get_json()["task_id"]
            app.extensions["scan_coordinator"].wait(task_id, timeout=5)
            conn = sqlite3.connect(db_path)
            photo_id = conn.execute("SELECT id FROM photos").fetchone()[0]
            conn.close()
            source.unlink()
            task_id = client.post("/api/library/scan").get_json()["task_id"]
            app.extensions["scan_coordinator"].wait(task_id, timeout=5)

            archived = client.post(
                "/api/library/archive", json={"photo_ids": [photo_id], "archived": True}
            )
            self.assertEqual(archived.status_code, 200)
            self.assertEqual(archived.get_json()["updated"], 1)
            payload = client.get("/api/library?visibility_status=archived").get_json()
            self.assertEqual(payload["items"][0]["photo_id"], photo_id)
            self.assertEqual(payload["items"][0]["file_status"], "missing")

            restored = client.post(
                "/api/library/archive", json={"photo_ids": [photo_id], "archived": False}
            )
            self.assertEqual(restored.status_code, 200)
            self.assertEqual(
                client.get("/api/library?visibility_status=active").get_json()["items"][0]["photo_id"],
                photo_id,
            )
            page = client.get("/library").get_data(as_text=True)
            self.assertIn('name="photo_ids"', page)
            self.assertIn("归档所选", page)
            self.assertIn('name="visibility_status"', page)
            app.extensions["scan_coordinator"].shutdown()


if __name__ == "__main__":
    unittest.main()
