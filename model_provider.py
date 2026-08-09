#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Small OpenAI-compatible client used by the settings diagnostics."""

from __future__ import annotations

from typing import Any

import requests


_ONE_PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class ModelProviderClient:
    def __init__(self, *, http=requests) -> None:
        self.http = http

    @staticmethod
    def _root(channel: dict[str, Any]) -> str:
        value = str(channel.get("base_url") or channel.get("api_url") or "").rstrip("/")
        suffix = "/chat/completions"
        return value[: -len(suffix)] if value.endswith(suffix) else value

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    @staticmethod
    def _timeout(channel: dict[str, Any]) -> float:
        return max(1.0, float(channel.get("timeout") or 60))

    def discover_models(self, channel: dict[str, Any], api_key: str) -> dict[str, Any]:
        try:
            response = self.http.get(
                f"{self._root(channel)}/models",
                headers=self._headers(api_key),
                timeout=self._timeout(channel),
            )
            response.raise_for_status()
            data = response.json().get("data") or []
            models = [
                {"model_id": str(item["id"]), "name": str(item.get("name") or item["id"])}
                for item in data
                if isinstance(item, dict) and item.get("id")
            ]
            return {"ok": True, "models": models}
        except Exception:
            return {"ok": False, "error": "request_failed", "models": []}

    def test_connection(self, channel: dict[str, Any], api_key: str) -> dict[str, Any]:
        result = self.discover_models(channel, api_key)
        if result["ok"]:
            return {"ok": True, "test": "connection"}
        return {"ok": False, "test": "connection", "error": result["error"]}

    def test_vision(
        self, channel: dict[str, Any], model_id: str, api_key: str
    ) -> dict[str, Any]:
        try:
            response = self.http.post(
                f"{self._root(channel)}/chat/completions",
                headers={"Content-Type": "application/json", **self._headers(api_key)},
                timeout=self._timeout(channel),
                json={
                    "model": model_id,
                    "max_tokens": 8,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Describe this image briefly."},
                                {"type": "image_url", "image_url": {"url": _ONE_PIXEL_PNG}},
                            ],
                        }
                    ],
                },
            )
            response.raise_for_status()
            choices = response.json().get("choices") or []
            if not choices:
                return {"ok": False, "test": "vision", "error": "invalid_response", "model_id": model_id}
            return {"ok": True, "test": "vision", "model_id": model_id}
        except Exception:
            return {"ok": False, "test": "vision", "error": "request_failed", "model_id": model_id}
