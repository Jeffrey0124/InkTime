import importlib
import sys
import types
import unittest


class _FakeResponse:
    def __init__(self, content: str):
        self.ok = True
        self.status_code = 200
        self.text = content
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class AnalyzeChannelFallbackTests(unittest.TestCase):
    def _load_module(self):
        fake_config = types.ModuleType("config")
        fake_config.IMAGE_DIR = "."
        fake_config.DB_PATH = ":memory:"
        fake_config.API_CHANNELS = []
        sys.modules["config"] = fake_config
        sys.modules.pop("analyze_photos", None)
        return importlib.import_module("analyze_photos")

    def test_parse_failure_falls_back_to_cloud_channel(self):
        mod = self._load_module()
        mod.API_CHANNELS = [
            {
                "name": "local_lmstudio",
                "api_url": "http://local/v1/chat/completions",
                "api_key": "",
                "model_name": "google/gemma-4-31b-qat:2",
            },
            {
                "name": "cloud_qwen",
                "api_url": "http://cloud/v1/chat/completions",
                "api_key": "test",
                "model_name": "qwen3-vl-plus",
            },
        ]
        mod._channel_cooldown_until = [0.0, 0.0]
        mod._channel_inflight = [0, 0]
        calls = []

        def fake_post(url, headers, json, timeout):
            calls.append(url)
            if "local" in url:
                return _FakeResponse("不是 JSON")
            return _FakeResponse('{"ok": true}')

        old_post = mod.requests.post
        try:
            mod.requests.post = fake_post
            parsed, channel = mod._post_with_channel_fallback(
                lambda ch: (ch["api_url"], {}, {}),
                timeout=1,
                response_parser=lambda resp: resp.json()["choices"][0]["message"]["content"]
                if resp.json()["choices"][0]["message"]["content"].startswith("{")
                else (_ for _ in ()).throw(ValueError("bad json")),
                return_channel=True,
            )
        finally:
            mod.requests.post = old_post

        self.assertEqual(calls, ["http://local/v1/chat/completions", "http://cloud/v1/chat/completions"])
        self.assertEqual(parsed, '{"ok": true}')
        self.assertEqual(channel["analysis_channel"], "cloud_qwen")
        self.assertEqual(channel["analysis_model"], "qwen3-vl-plus")

    def test_crop_focus_accepts_qwen_box_tag(self):
        mod = self._load_module()

        focus = mod._normalize_crop_focus(
            '<box>(180,480,630,780)</box>',
            image_width=1000,
            image_height=1000,
        )

        self.assertEqual(
            focus,
            {
                "x": 0.48,
                "y": 0.18,
                "w": 0.3,
                "h": 0.45,
                "reason": "视觉定位框",
            },
        )

    def test_extract_json_object_ignores_outer_box_text(self):
        mod = self._load_module()

        obj = mod._extract_json_object(
            '说明 <box>(180,480,630,780)</box> {"caption":"ok","type":"日常","memory_score":80,"beauty_score":70,"reason":"主体清楚"}'
        )

        self.assertEqual(obj["caption"], "ok")

    def test_gps_dms_float_tuple_is_converted(self):
        mod = self._load_module()

        lat = mod._convert_gps_to_deg((31.0, 15.0, 2.68))
        lon = mod._convert_gps_to_deg((121.0, 26.0, 28.46))

        self.assertAlmostEqual(lat, 31.2507444, places=6)
        self.assertAlmostEqual(lon, 121.4412389, places=6)


if __name__ == "__main__":
    unittest.main()
