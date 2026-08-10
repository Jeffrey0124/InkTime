import contextlib
import io
import unittest
from unittest.mock import patch

import scheduler


class SchedulerTests(unittest.TestCase):
    def test_success_log_does_not_expose_source_path(self):
        manifest = {
            "image_url": "/push/latest.bmp",
            "source_path": "/photos/private/family.jpg",
        }
        output = io.StringIO()

        with patch.object(scheduler, "publish_scheduled", return_value=manifest):
            with contextlib.redirect_stdout(output):
                scheduler.run_scheduled_push("07:00")

        message = output.getvalue()
        self.assertIn("/push/latest.bmp", message)
        self.assertIn("slot=07:00", message)
        self.assertNotIn("source_path", message)
        self.assertNotIn("family.jpg", message)


if __name__ == "__main__":
    unittest.main()
