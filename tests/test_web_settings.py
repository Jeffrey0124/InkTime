#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import tempfile
import unittest
from pathlib import Path

from server import _dispatch_channel_diagnostic, create_app


class FakeProvider:
    def discover_models(self, channel, api_key):
        return {"ok": True, "models": [{"model_id": "vision-a", "name": "Vision A"}]}

    def test_connection(self, channel, api_key):
        return {"ok": True, "test": "connection"}

    def test_vision(self, channel, model_id, api_key):
        return {"ok": True, "test": "vision", "model_id": model_id}


class WebSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.app = create_app(
            db_path=root / "photos.db",
            render_output_dir=root / "renders",
            auth_required=False,
            settings_master_key="test-master-key",
            model_provider=FakeProvider(),
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_settings_page_has_four_independent_tabs_and_new_task_notice(self):
        response = self.client.get("/settings")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        for label in ("模型通道", "分析默认值", "素材扫描", "安全"):
            self.assertIn(label, html)
        self.assertIn("仅影响新进程/新任务", html)
        self.assertIn("data-settings-app", html)

    def test_channel_api_never_returns_secret_and_exposes_presets(self):
        response = self.client.post(
            "/api/settings/model-channels",
            json={
                "name": "主视觉通道",
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "credential": {"source": "database", "value": "secret-value"},
                "models": [{"model_id": "vision-a", "name": "Vision A", "is_default": True}],
            },
        )
        body = response.get_json()

        self.assertEqual(response.status_code, 201)
        self.assertNotIn("secret-value", response.get_data(as_text=True))
        self.assertEqual(body["channel"]["credential"], {"source": "database", "configured": True})
        listing = self.client.get("/api/settings/model-channels").get_json()
        self.assertEqual(
            [preset["id"] for preset in listing["presets"]],
            ["lm_studio", "deepseek", "qwen", "openai_compatible", "custom"],
        )

    def test_discovery_and_connection_and_vision_tests_are_independent(self):
        created = self.client.post(
            "/api/settings/model-channels",
            json={
                "name": "本地通道",
                "provider": "lm_studio",
                "base_url": "http://127.0.0.1:1234/v1",
                "credential": {"source": "none"},
                "models": [],
            },
        ).get_json()["channel"]

        discovered = self.client.post(
            f"/api/settings/model-channels/{created['id']}/discover"
        ).get_json()
        connection = self.client.post(
            f"/api/settings/model-channels/{created['id']}/test-connection"
        ).get_json()
        vision = self.client.post(
            f"/api/settings/model-channels/{created['id']}/test-vision",
            json={"model_id": "vision-a"},
        ).get_json()

        self.assertEqual(discovered["models"][0]["model_id"], "vision-a")
        self.assertEqual(connection["test"], "connection")
        self.assertEqual(vision["test"], "vision")

    def test_fallback_chain_preserves_channel_and_model_order(self):
        channel_ids = []
        for name, model_id in (("A", "a-model"), ("B", "b-model")):
            channel = self.client.post(
                "/api/settings/model-channels",
                json={
                    "name": name,
                    "provider": "custom",
                    "base_url": "https://example.com/v1",
                    "credential": {"source": "none"},
                    "models": [{"model_id": model_id, "name": model_id, "is_default": True}],
                },
            ).get_json()["channel"]
            channel_ids.append(channel["id"])

        items = [
            {"channel_id": channel_ids[1], "model_id": "b-model"},
            {"channel_id": channel_ids[0], "model_id": "a-model"},
        ]
        response = self.client.put("/api/settings/fallback-chain", json={"items": items})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["items"], items)
        self.assertEqual(self.client.get("/api/settings/fallback-chain").get_json()["items"], items)

    def test_each_non_sensitive_section_is_saved_manually_and_versioned(self):
        first = self.client.put(
            "/api/settings/analysis-defaults", json={"batch_size": 10, "max_long_edge": 2048}
        ).get_json()
        second = self.client.put(
            "/api/settings/analysis-defaults", json={"batch_size": 20, "max_long_edge": 1600}
        ).get_json()

        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        versions = self.client.get("/api/settings/versions/analysis_defaults").get_json()
        self.assertEqual([item["version"] for item in versions["versions"]], [2, 1])

    def test_unknown_diagnostic_kind_is_rejected(self):
        result, status = _dispatch_channel_diagnostic(
            self.app.extensions["settings_store"],
            FakeProvider(),
            "missing",
            "not-a-kind",
            {},
        )
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "unknown_diagnostic")

    def test_settings_javascript_tracks_sections_and_adds_local_draft(self):
        source = (Path(__file__).parents[1] / "static" / "app.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("const dirtySections = new Set()", source)
        for key in (
            "fallback_chain",
            "analysis_defaults",
            "scan_settings",
            "security_settings",
        ):
            self.assertIn(key, source)
        self.assertIn('id: `draft-${', source)
        self.assertIn('channel._draft ? "POST" : "PUT"', source)


if __name__ == "__main__":
    unittest.main()
