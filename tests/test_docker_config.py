import importlib.util
import os
import unittest
from pathlib import Path


class DockerConfigTests(unittest.TestCase):
    def test_docker_config_reads_environment_values(self):
        config_path = Path(__file__).resolve().parents[1] / "docker" / "config.py"
        old_env = os.environ.copy()
        try:
            os.environ.update(
                {
                    "LOCAL_VLM_API_URL": "http://192.168.1.50:9100/v1/chat/completions",
                    "LOCAL_VLM_MODEL": "google/gemma-4-31b-qat:2",
                    "CLOUD_QWEN_API_URL": "https://example.test/compatible-mode/v1/chat/completions",
                    "CLOUD_QWEN_API_KEY": "test-key",
                    "PUSH_SCHEDULES": "07:00,12:00,18:40",
                    "INKTIME_PUSH_API_TOKEN": "push-token",
                    "BATCH_LIMIT": "12",
                    "DAILY_PHOTO_QUANTITY": "3",
                }
            )
            spec = importlib.util.spec_from_file_location("docker_config_under_test", config_path)
            self.assertIsNotNone(spec)
            module = importlib.util.module_from_spec(spec)
            self.assertIsNotNone(spec.loader)
            spec.loader.exec_module(module)

            self.assertEqual(module.IMAGE_DIR, "/photos")
            self.assertEqual(module.DB_PATH, "/data/photos.db")
            self.assertEqual(module.RENDER_OUTPUT_DIR, "/data/output/photopainter")
            self.assertEqual(module.PUSH_OUTPUT_DIR, "/data/output/push")
            self.assertEqual(module.FLASK_HOST, "0.0.0.0")
            self.assertEqual(module.FLASK_PORT, 8766)
            self.assertEqual(module.PUSH_SCHEDULES, ["07:00", "12:00", "18:40"])
            self.assertEqual(module.PUSH_API_TOKEN, "push-token")
            self.assertEqual(module.BATCH_LIMIT, 12)
            self.assertEqual(module.DAILY_PHOTO_QUANTITY, 3)
            self.assertEqual(module.API_CHANNELS[0]["name"], "local_lmstudio")
            self.assertEqual(module.API_CHANNELS[0]["api_url"], "http://192.168.1.50:9100/v1/chat/completions")
            self.assertEqual(module.API_CHANNELS[1]["name"], "cloud_qwen")
            self.assertEqual(module.API_CHANNELS[1]["api_key"], "test-key")
        finally:
            os.environ.clear()
            os.environ.update(old_env)


if __name__ == "__main__":
    unittest.main()
