#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis_settings import load_analysis_runtime_settings
from settings_store import SettingsStore


class AnalysisRuntimeSettingsTests(unittest.TestCase):
    def _configured_database(self, root: Path) -> Path:
        db_path = root / "photos.db"
        store = SettingsStore(db_path, master_key="test-master-key")
        channel = store.save_channel(
            {
                "name": "家庭视觉主通道",
                "provider": "custom",
                "base_url": "https://vision.example/v1",
                "timeout": 42,
                "credential": {"source": "none"},
                "models": [
                    {"model_id": "vision-a", "name": "Vision A", "is_default": True}
                ],
            }
        )
        store.save_fallback_chain(
            [{"channel_id": channel["id"], "model_id": "vision-a"}]
        )
        store.save_analysis_defaults(
            {"batch_size": 7, "max_long_edge": 1536, "prompt_profile": "memory"}
        )
        return db_path

    def test_database_chain_and_analysis_defaults_override_config_for_new_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._configured_database(Path(tmp))

            runtime = load_analysis_runtime_settings(
                db_path,
                config_channels=[{"name": "config-fallback"}],
                config_defaults={"batch_size": 1, "max_long_edge": 2560},
                master_key="test-master-key",
            )

            self.assertEqual(runtime["source"], "database")
            self.assertEqual(runtime["channels"][0]["name"], "家庭视觉主通道")
            self.assertEqual(runtime["channels"][0]["model_name"], "vision-a")
            self.assertEqual(runtime["defaults"]["batch_size"], 7)
            self.assertEqual(runtime["defaults"]["max_long_edge"], 1536)

    def test_empty_database_chain_keeps_config_channels(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "photos.db"
            SettingsStore(db_path, master_key="test-master-key")
            fallback = [{"name": "config-fallback", "model_name": "config-model"}]

            runtime = load_analysis_runtime_settings(
                db_path,
                config_channels=fallback,
                config_defaults={"batch_size": 3},
                master_key="test-master-key",
            )

            self.assertEqual(runtime["source"], "config")
            self.assertEqual(runtime["channels"], fallback)
            self.assertEqual(runtime["defaults"], {"batch_size": 3})

    def test_unreadable_database_credential_falls_back_channels_but_keeps_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "photos.db"
            store = SettingsStore(db_path, master_key="write-key")
            channel = store.save_channel(
                {
                    "name": "加密通道",
                    "provider": "custom",
                    "base_url": "https://example.com/v1",
                    "credential": {"source": "database", "value": "secret"},
                    "models": [
                        {"model_id": "vision", "name": "Vision", "is_default": True}
                    ],
                }
            )
            store.save_fallback_chain(
                [{"channel_id": channel["id"], "model_id": "vision"}]
            )
            store.save_analysis_defaults({"batch_size": 9})

            runtime = load_analysis_runtime_settings(
                db_path,
                config_channels=[{"name": "config-fallback"}],
                config_defaults={"batch_size": 1},
                master_key="wrong-key",
            )

            self.assertEqual(runtime["channels"], [{"name": "config-fallback"}])
            self.assertEqual(runtime["defaults"]["batch_size"], 9)

    def test_fresh_analyze_module_import_reads_database_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._configured_database(Path(tmp))
            fake_config = types.ModuleType("config")
            fake_config.IMAGE_DIR = tmp
            fake_config.DB_PATH = str(db_path)
            fake_config.API_CHANNELS = [{"name": "config-fallback"}]
            fake_config.BATCH_LIMIT = 1
            fake_config.VLM_MAX_LONG_EDGE = 2560
            fake_config.SETTINGS_MASTER_KEY = "test-master-key"

            with patch.dict(sys.modules, {"config": fake_config}):
                sys.modules.pop("analyze_photos", None)
                module = importlib.import_module("analyze_photos")

            self.assertEqual(module.API_CHANNELS[0]["name"], "家庭视觉主通道")
            self.assertEqual(module.BATCH_LIMIT, 7)
            self.assertEqual(module.VLM_MAX_LONG_EDGE, 1536)
            self.assertEqual(module.ANALYSIS_PROMPT_PROFILE, "memory")
            self.assertIn("回忆优先方案", module.analysis_prompt_profile_instruction())
            self.assertIn(
                "美观优先方案", module.analysis_prompt_profile_instruction("beauty")
            )
            sys.modules.pop("analyze_photos", None)


if __name__ == "__main__":
    unittest.main()
