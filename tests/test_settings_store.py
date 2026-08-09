#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import inspect
import tempfile
import unittest
from pathlib import Path

from settings_store import MasterKeyUnavailable, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "photos.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_credential_is_encrypted_and_never_returned(self):
        store = SettingsStore(self.db_path, master_key="test-master-key")

        channel = store.save_channel(
            {
                "name": "家庭 DeepSeek",
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "timeout": 45,
                "credential": {"source": "database", "value": "secret-value"},
                "models": [
                    {"model_id": "deepseek-vl", "name": "DeepSeek VL", "is_default": True}
                ],
            }
        )

        self.assertEqual(channel["credential"], {"source": "database", "configured": True})
        self.assertNotIn("secret-value", repr(channel))
        self.assertEqual(store.resolve_credential(channel["id"]), "secret-value")
        conn = sqlite3.connect(self.db_path)
        try:
            raw = conn.execute(
                "SELECT credential_ciphertext FROM model_channels WHERE id = ?",
                (channel["id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertNotIn(b"secret-value", raw)

        wrong_key_store = SettingsStore(self.db_path, master_key="different-master-key")
        with self.assertRaises(Exception):
            wrong_key_store.resolve_credential(channel["id"])

    def test_database_credential_write_requires_master_key_but_non_sensitive_save_works(self):
        store = SettingsStore(self.db_path, master_key="")
        channel = store.save_channel(
            {
                "name": "LM Studio",
                "provider": "lm_studio",
                "base_url": "http://127.0.0.1:1234/v1",
                "credential": {"source": "none"},
                "models": [],
            }
        )
        self.assertFalse(store.capabilities()["database_credentials"])
        with self.assertRaises(MasterKeyUnavailable):
            store.save_channel(
                {
                    **channel,
                    "credential": {"source": "database", "value": "secret-value"},
                }
            )

    def test_non_sensitive_saves_create_immutable_versions(self):
        store = SettingsStore(self.db_path, master_key="test-master-key")
        first = store.save_analysis_defaults(
            {"batch_size": 10, "max_long_edge": 2048, "prompt_profile": "balanced"}
        )
        second = store.save_analysis_defaults(
            {"batch_size": 20, "max_long_edge": 1600, "prompt_profile": "balanced"}
        )

        self.assertEqual(first["version"], 1)
        self.assertEqual(second["version"], 2)
        versions = store.list_versions("analysis_defaults")
        self.assertEqual([item["version"] for item in versions], [2, 1])
        self.assertEqual(versions[1]["snapshot"]["batch_size"], 10)

    def test_historically_referenced_channel_can_only_be_disabled(self):
        store = SettingsStore(self.db_path, master_key="test-master-key")
        channel = store.save_channel(
            {
                "name": "旧通道",
                "provider": "openai_compatible",
                "base_url": "https://example.com/v1",
                "credential": {"source": "none"},
                "models": [{"model_id": "vision-1", "name": "Vision 1", "is_default": True}],
            }
        )
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("CREATE TABLE photo_scores (analysis_channel TEXT, analysis_model TEXT)")
            conn.execute(
                "INSERT INTO photo_scores VALUES (?, ?)",
                (channel["name"], "vision-1"),
            )
            conn.commit()
        finally:
            conn.close()

        result = store.delete_channel(channel["id"])

        self.assertEqual(result["result"], "disabled")
        self.assertFalse(result["channel"]["enabled"])

    def test_historically_referenced_removed_model_is_retained_as_disabled(self):
        store = SettingsStore(self.db_path, master_key="test-master-key")
        channel = store.save_channel(
            {
                "name": "家庭视觉",
                "provider": "custom",
                "base_url": "https://example.com/v1",
                "credential": {"source": "none"},
                "models": [
                    {"model_id": "old-vision", "name": "Old Vision", "is_default": True},
                    {"model_id": "new-vision", "name": "New Vision"},
                ],
            }
        )
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("CREATE TABLE photo_scores (analysis_channel TEXT, analysis_model TEXT)")
            conn.execute("INSERT INTO photo_scores VALUES (?, ?)", (channel["name"], "old-vision"))
            conn.commit()
        finally:
            conn.close()

        updated = store.save_channel(
            {
                **channel,
                "models": [
                    {"model_id": "new-vision", "name": "New Vision", "is_default": True}
                ],
            }
        )

        models = {item["model_id"]: item for item in updated["models"]}
        self.assertFalse(models["old-vision"]["enabled"])
        self.assertTrue(models["new-vision"]["enabled"])

    def test_settings_store_uses_photo_identity_model_schema(self):
        SettingsStore(self.db_path, master_key="test-master-key")
        conn = sqlite3.connect(self.db_path)
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(model_channels)")
            }
        finally:
            conn.close()
        self.assertIn("provider_preset", columns)
        self.assertIn("current_version_id", columns)
        source = inspect.getsource(SettingsStore.ensure_schema)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS model_channels", source)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS model_channel_versions", source)


if __name__ == "__main__":
    unittest.main()
