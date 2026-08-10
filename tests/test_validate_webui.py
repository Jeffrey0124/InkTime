import io
import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image


ROOT_DIR = Path(__file__).resolve().parents[1]


def _bmp_bytes(*, size=(800, 480), extra_color=None) -> bytes:
    image = Image.new("RGB", size, (255, 255, 255))
    image.putpixel((0, 0), (0, 0, 0))
    image.putpixel((1, 0), (255, 0, 0))
    image.putpixel((2, 0), (255, 255, 0))
    image.putpixel((3, 0), (0, 0, 255))
    image.putpixel((4, 0), (0, 255, 0))
    if extra_color is not None:
        image.putpixel((5, 0), extra_color)
    output = io.BytesIO()
    image.save(output, format="BMP")
    return output.getvalue()


class _ValidationHandler(BaseHTTPRequestHandler):
    health_ok = True
    manifest_image_url = "/push/latest.bmp"
    bmp_size = (800, 480)
    extra_color = None
    requested_paths = []
    redirect_management = False

    def do_GET(self):
        path = urlparse(self.path).path
        type(self).requested_paths.append(self.path)
        if path == "/healthz":
            payload = json.dumps({"ok": type(self).health_ok}).encode("utf-8")
            self._send("application/json", payload)
        elif path == "/api/photos":
            payload = json.dumps({"ok": True, "photos": [{"photo_id": 7}]}).encode("utf-8")
            self._send("application/json", payload)
        elif path == "/push/manifest.json":
            payload = json.dumps(
                {"image_url": type(self).manifest_image_url}
            ).encode("utf-8")
            self._send("application/json", payload)
        elif path == "/push/latest.bmp":
            self._send(
                "image/bmp",
                _bmp_bytes(
                    size=type(self).bmp_size,
                    extra_color=type(self).extra_color,
                ),
            )
        elif path == "/push/latest.png":
            self._send("image/png", b"png-preview")
        elif type(self).redirect_management and path in {"/", "/library", "/analysis-tasks", "/push-studio/7"}:
            self.send_response(302)
            self.send_header("Location", f"/login?next={path}")
            self.end_headers()
        elif path in {"/", "/gallery", "/library", "/analysis-tasks", "/photos/7", "/push-studio/7"}:
            self._send("text/html; charset=utf-8", b"<html>InkTime</html>")
        else:
            self.send_error(404)

    def _send(self, content_type, body):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


class ValidateWebUITests(unittest.TestCase):
    def setUp(self):
        _ValidationHandler.health_ok = True
        _ValidationHandler.manifest_image_url = "/push/latest.bmp"
        _ValidationHandler.bmp_size = (800, 480)
        _ValidationHandler.extra_color = None
        _ValidationHandler.requested_paths = []
        _ValidationHandler.redirect_management = False

    def _run_cli(self, *extra_args):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ValidationHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            return subprocess.run(
                [
                    sys.executable,
                    "scripts/validate_webui.py",
                    "--base-url",
                    f"http://127.0.0.1:{server.server_port}",
                    *extra_args,
                ],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_cli_validates_concrete_routes_and_six_color_bmp(self):
        result = self._run_cli()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS: WebUI routes and push artifact", result.stdout)
        self.assertIn("/api/photos?limit=1", _ValidationHandler.requested_paths)
        self.assertIn("/library", _ValidationHandler.requested_paths)
        self.assertIn("/analysis-tasks", _ValidationHandler.requested_paths)
        self.assertIn("/photos/7", _ValidationHandler.requested_paths)
        self.assertIn("/push-studio/7", _ValidationHandler.requested_paths)

    def test_cli_rejects_invalid_acceptance_artifacts(self):
        cases = (
            ("health_ok", False, "/healthz did not return"),
            ("manifest_image_url", "/wrong.bmp", "image_url must be"),
            ("bmp_size", (640, 480), "latest.bmp size is"),
            ("extra_color", (12, 34, 56), "non-PhotoPainter colors"),
        )
        for attribute, value, message in cases:
            with self.subTest(attribute=attribute):
                self.setUp()
                setattr(_ValidationHandler, attribute, value)
                result = self._run_cli()
                self.assertEqual(result.returncode, 1)
                self.assertIn(message, result.stderr)

    def test_cli_validates_login_redirects_without_following_them(self):
        _ValidationHandler.redirect_management = True

        result = self._run_cli("--expect-auth")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"auth_boundary": "required"', result.stdout)

    def test_cli_rejects_unprotected_management_routes_when_auth_is_expected(self):
        result = self._run_cli("--expect-auth")

        self.assertEqual(result.returncode, 1)
        self.assertIn("did not redirect to /login", result.stderr)


if __name__ == "__main__":
    unittest.main()
