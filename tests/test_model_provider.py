#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from unittest.mock import Mock

from model_provider import ModelProviderClient


class ModelProviderClientTests(unittest.TestCase):
    def setUp(self):
        self.http = Mock()
        self.client = ModelProviderClient(http=self.http)
        self.channel = {
            "base_url": "http://127.0.0.1:1234/v1",
            "timeout": 15,
        }

    def test_discovers_multiple_models_from_openai_compatible_endpoint(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": [{"id": "vision-a"}, {"id": "text-b"}]
        }
        self.http.get.return_value = response

        result = self.client.discover_models(self.channel, "token")

        self.assertTrue(result["ok"])
        self.assertEqual([item["model_id"] for item in result["models"]], ["vision-a", "text-b"])
        self.http.get.assert_called_once_with(
            "http://127.0.0.1:1234/v1/models",
            headers={"Authorization": "Bearer token"},
            timeout=15.0,
        )

    def test_basic_and_visual_tests_are_separate_requests(self):
        basic_response = Mock()
        basic_response.raise_for_status.return_value = None
        basic_response.json.return_value = {"data": []}
        visual_response = Mock()
        visual_response.raise_for_status.return_value = None
        visual_response.json.return_value = {
            "choices": [{"message": {"content": "image"}}]
        }
        self.http.get.return_value = basic_response
        self.http.post.return_value = visual_response

        basic = self.client.test_connection(self.channel, "token")
        visual = self.client.test_vision(self.channel, "vision-a", "token")

        self.assertEqual(basic, {"ok": True, "test": "connection"})
        self.assertEqual(visual, {"ok": True, "test": "vision", "model_id": "vision-a"})
        self.http.get.assert_called_once()
        body = self.http.post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "vision-a")
        self.assertEqual(body["messages"][0]["content"][1]["type"], "image_url")

    def test_failures_are_returned_as_safe_results(self):
        self.http.get.side_effect = RuntimeError("internal host detail")

        result = self.client.discover_models(self.channel, "")

        self.assertEqual(result, {"ok": False, "error": "request_failed", "models": []})
        self.assertNotIn("internal host detail", repr(result))


if __name__ == "__main__":
    unittest.main()
