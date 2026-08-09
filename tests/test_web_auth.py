import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from photo_identity import ensure_photo_identity_schema
from server import create_app


class MutableClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = MutableClock()
        self.app = create_app(
            db_path=self.root / "photos.db",
            render_output_dir=self.root / "renders",
            auth_required=True,
            initial_admin_password="initial-pass",
            session_secret="test-session-secret",
            auth_now=self.clock.now,
        )
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def csrf(self, client=None) -> str:
        active = client or self.client
        response = active.get("/api/auth/session")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["csrf_token"]

    def login(self, password: str, client=None):
        active = client or self.client
        return active.post(
            "/api/auth/login",
            json={"password": password},
            headers={"X-CSRF-Token": self.csrf(active)},
        )

    def change_password(self, current: str, new: str, client=None):
        active = client or self.client
        return active.post(
            "/api/auth/change-password",
            json={"current_password": current, "new_password": new},
            headers={"X-CSRF-Token": self.csrf(active)},
        )

    def test_visitor_can_browse_gallery_but_management_requires_login(self):
        self.assertEqual(self.client.get("/gallery").status_code, 200)
        management = self.client.get("/", follow_redirects=False)
        self.assertEqual(management.status_code, 302)
        self.assertIn("/login?next=/", management.headers["Location"])
        api = self.client.get("/api/status")
        self.assertEqual(api.status_code, 401)
        self.assertEqual(api.get_json()["error"], "authentication_required")

        gallery_html = self.client.get("/gallery").get_data(as_text=True)
        self.assertNotIn("推送工作台", gallery_html)
        self.assertNotIn("设置</span>", gallery_html)

    def test_initial_password_forces_change_then_allows_management(self):
        logged_in = self.login("initial-pass")
        self.assertEqual(logged_in.status_code, 200)
        self.assertTrue(logged_in.get_json()["must_change_password"])

        forced = self.client.get("/", follow_redirects=False)
        self.assertEqual(forced.status_code, 302)
        self.assertEqual(forced.headers["Location"], "/change-password")

        unchanged = self.change_password("initial-pass", "initial-pass")
        self.assertEqual(unchanged.status_code, 400)
        self.assertEqual(unchanged.get_json()["error"], "password_unchanged")

        too_short = self.change_password("initial-pass", "short")
        self.assertEqual(too_short.status_code, 400)
        changed = self.change_password("initial-pass", "new-password")
        self.assertEqual(changed.status_code, 200)
        self.assertFalse(changed.get_json()["must_change_password"])
        self.assertEqual(self.client.get("/").status_code, 200)

        self.client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": self.csrf()}
        )
        self.assertEqual(self.login("initial-pass").status_code, 401)
        self.clock.advance(seconds=1)
        self.assertEqual(self.login("new-password").status_code, 200)

    def test_csrf_is_required_for_authenticated_writes(self):
        self.login("initial-pass")
        response = self.client.post(
            "/api/auth/change-password",
            json={"current_password": "initial-pass", "new_password": "new-password"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "csrf_failed")

    def test_password_change_revokes_other_sessions(self):
        self.login("initial-pass")
        self.change_password("initial-pass", "shared-password")

        other = self.app.test_client()
        self.assertEqual(self.login("shared-password", other).status_code, 200)
        self.assertEqual(self.change_password("shared-password", "final-password").status_code, 200)

        revoked = other.get("/api/status")
        self.assertEqual(revoked.status_code, 401)

    def test_idle_and_absolute_session_expiry(self):
        self.login("initial-pass")
        self.clock.advance(hours=2, seconds=1)
        self.assertEqual(self.client.get("/api/status").status_code, 401)

        self.clock.advance(seconds=1)
        self.assertEqual(self.login("initial-pass").status_code, 200)
        for _ in range(12):
            self.clock.advance(hours=1, minutes=59)
            self.assertEqual(self.client.get("/api/auth/session").status_code, 200)
        self.clock.advance(minutes=13)
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_login_failures_use_progressive_delay_without_permanent_lockout(self):
        first = self.login("wrong-password")
        self.assertEqual(first.status_code, 401)
        delayed = self.login("wrong-password")
        self.assertEqual(delayed.status_code, 429)
        self.assertGreater(delayed.get_json()["retry_after"], 0)

        self.clock.advance(seconds=2)
        success = self.login("initial-pass")
        self.assertEqual(success.status_code, 200)

    def test_login_page_contains_csrf_and_never_echoes_password(self):
        page = self.client.get("/login")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertRegex(html, re.compile(r'name="csrf_token" value="[^"]+"'))
        self.assertNotIn("initial-pass", html)

        conn = sqlite3.connect(self.root / "photos.db")
        try:
            account = conn.execute(
                "SELECT password_salt, password_hash FROM admin_account"
            ).fetchone()
        finally:
            conn.close()
        self.assertNotIn(b"initial-pass", bytes(account[0]) + bytes(account[1]))

    def test_guest_preview_hides_original_path_and_management_actions(self):
        source = self.root / "private-source.jpg"
        Image.new("RGB", (120, 80), (40, 160, 90)).save(source)
        conn = sqlite3.connect(self.root / "photos.db")
        conn.execute(
            """
            CREATE TABLE photo_scores (
              path TEXT PRIMARY KEY,
              caption TEXT,
              type TEXT,
              memory_score REAL,
              beauty_score REAL,
              reason TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO photo_scores VALUES (?, 'caption', 'daily', 80, 70, 'reason')",
            (str(source),),
        )
        conn.commit()
        conn.close()
        ensure_photo_identity_schema(self.root / "photos.db")
        location_patch = patch("web_queries.read_location_from_source", return_value={})
        location_patch.start()
        self.addCleanup(location_patch.stop)

        listing = self.client.get("/api/photos")
        payload = listing.get_json()["photos"][0]
        self.assertNotIn("path", payload)
        self.assertNotIn(str(self.root), listing.get_data(as_text=True))
        self.assertRegex(payload["source_url"], r"^/media/previews/\d+\.jpg$")

        with self.client.get(payload["source_url"]) as preview:
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.mimetype, "image/jpeg")
        original = self.client.get(f"/api/photos/{payload['photo_id']}/source")
        self.assertEqual(original.status_code, 401)
        self.assertEqual(self.client.get("/push/latest.bmp").status_code, 404)
        self.assertEqual(self.client.get("/static/renders/example.png").status_code, 302)

        detail = self.client.get(f"/photos/{payload['photo_id']}")
        detail_html = detail.get_data(as_text=True)
        self.assertNotIn("进入推送工作台", detail_html)
        self.assertNotIn("文案微调", detail_html)

    def test_permission_matrix_and_session_cookie_flags(self):
        protected_pages = ("/", "/review", "/renders", "/push-studio", "/settings")
        for path in protected_pages:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 302)

        response = self.login("initial-pass")
        cookie = response.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.change_password("initial-pass", "new-password")

        directly_rendered_pages = ("/", "/renders", "/settings")
        for path in directly_rendered_pages:
            with self.subTest(path=path):
                self.assertNotIn(self.client.get(path).status_code, (302, 401, 403))

        for path in ("/push-studio",):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertFalse(
                    response.status_code == 302
                    and response.headers.get("Location", "").startswith("/login")
                )

        write_without_csrf = self.client.patch(
            "/api/photos/999/overrides", json={"caption": "test"}
        )
        self.assertEqual(write_without_csrf.status_code, 400)
        self.assertEqual(write_without_csrf.get_json()["error"], "csrf_failed")

    def test_login_next_rejects_external_redirect(self):
        response = self.client.get("/login?next=//example.com", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('value="//example.com"', html)

    def test_generated_session_secret_is_stable_for_the_database(self):
        first = create_app(
            db_path=self.root / "stable.db",
            render_output_dir=self.root / "renders-a",
            auth_required=True,
            initial_admin_password="initial-pass",
            session_secret="",
        )
        second = create_app(
            db_path=self.root / "stable.db",
            render_output_dir=self.root / "renders-b",
            auth_required=True,
            initial_admin_password="initial-pass",
            session_secret="",
        )
        self.assertEqual(first.secret_key, second.secret_key)

    def test_https_mode_marks_session_cookie_secure(self):
        app = create_app(
            db_path=self.root / "secure.db",
            render_output_dir=self.root / "secure-renders",
            auth_required=True,
            initial_admin_password="initial-pass",
            session_secret="secure-session-secret-for-tests",
            auth_cookie_secure=True,
        )
        app.config.update(TESTING=True)
        client = app.test_client()
        csrf = client.get("/api/auth/session").get_json()["csrf_token"]
        response = client.post(
            "/api/auth/login",
            json={"password": "initial-pass"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertIn("Secure", response.headers.get("Set-Cookie", ""))

    def test_reset_command_revokes_sessions_and_requires_change(self):
        self.login("initial-pass")
        runner = self.app.test_cli_runner()
        result = runner.invoke(
            args=["reset-admin-password", "--password", "reset-password"]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("reset-password", result.output)
        self.assertEqual(self.client.get("/api/status").status_code, 401)

        logged_in = self.login("reset-password")
        self.assertEqual(logged_in.status_code, 200)
        self.assertTrue(logged_in.get_json()["must_change_password"])

    def test_logout_ends_the_admin_session(self):
        self.login("initial-pass")
        self.change_password("initial-pass", "new-password")
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn('action="/logout"', page)
        response = self.client.post(
            "/logout",
            data={"csrf_token": self.csrf()},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/gallery")
        self.assertEqual(self.client.get("/api/status").status_code, 401)


if __name__ == "__main__":
    unittest.main()
