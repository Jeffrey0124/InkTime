#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Versioned WebUI settings built on the repository's model migrations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from photo_identity import ensure_photo_identity_schema


class SettingsError(ValueError):
    pass


class MasterKeyUnavailable(SettingsError):
    pass


PROVIDER_PRESETS = {
    "lm_studio": {"label": "LM Studio", "base_url": "http://127.0.0.1:1234/v1", "credential_source": "none"},
    "deepseek": {"label": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "credential_source": "environment", "credential_env": "DEEPSEEK_API_KEY"},
    "qwen": {"label": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "credential_source": "environment", "credential_env": "QWEN_API_KEY"},
    "openai_compatible": {"label": "OpenAI-compatible", "base_url": "https://api.openai.com/v1", "credential_source": "environment", "credential_env": "OPENAI_API_KEY"},
    "custom": {"label": "自定义", "base_url": "", "credential_source": "none"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


class SettingsStore:
    def __init__(self, db_path: str | Path, *, master_key: str | bytes = "") -> None:
        self.db_path = Path(db_path)
        self._master_key = master_key.encode() if isinstance(master_key, str) else master_key
        self.ensure_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        ensure_photo_identity_schema(self.db_path)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_fallback_chain (
                  position INTEGER PRIMARY KEY,
                  channel_id INTEGER NOT NULL,
                  model_id TEXT NOT NULL,
                  UNIQUE(channel_id, model_id),
                  FOREIGN KEY(channel_id) REFERENCES model_channels(id)
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                  key TEXT PRIMARY KEY,
                  value_json TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_setting_versions (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  section TEXT NOT NULL,
                  version INTEGER NOT NULL,
                  snapshot_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(section, version)
                );
                """
            )

    @staticmethod
    def provider_presets() -> list[dict[str, Any]]:
        return [{"id": key, **value} for key, value in PROVIDER_PRESETS.items()]

    def capabilities(self) -> dict[str, Any]:
        return {"database_credentials": bool(self._master_key), "warning": None if self._master_key else "未配置 INKTIME_SETTINGS_MASTER_KEY，数据库密钥写入已禁用。"}

    def _aead(self) -> AESGCM:
        if not self._master_key:
            raise MasterKeyUnavailable("未配置设置主密钥，不能保存数据库凭据")
        return AESGCM(hashlib.sha256(self._master_key).digest())

    def _encrypt(self, channel_id: str, value: str) -> bytes:
        nonce = secrets.token_bytes(12)
        cipher = self._aead().encrypt(nonce, value.encode(), f"inktime:model-channel:{channel_id}".encode())
        return base64.urlsafe_b64encode(nonce + cipher)

    def _decrypt(self, channel_id: str, value: bytes | str) -> str:
        encoded = value.encode() if isinstance(value, str) else value
        raw = base64.urlsafe_b64decode(encoded)
        return self._aead().decrypt(raw[:12], raw[12:], f"inktime:model-channel:{channel_id}".encode()).decode()

    def _version(self, conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row | None:
        if row["current_version_id"]:
            return conn.execute("SELECT * FROM model_channel_versions WHERE id=?", (row["current_version_id"],)).fetchone()
        return conn.execute("SELECT * FROM model_channel_versions WHERE channel_id=? ORDER BY version_number DESC LIMIT 1", (row["id"],)).fetchone()

    def _public_channel(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        version = self._version(conn, row)
        models = json.loads(version["models_json"]) if version else []
        default_model = str(version["default_model"] or "") if version else ""
        public_models = []
        for value in models:
            item = dict(value) if isinstance(value, dict) else {"model_id": str(value)}
            model_id = str(item.get("model_id") or item.get("id") or "")
            if not model_id:
                continue
            public_models.append({"model_id": model_id, "name": str(item.get("name") or model_id), "enabled": bool(item.get("enabled", True)), "is_default": model_id == default_model, "vision_capable": item.get("vision_capable")})
        source = str(row["credential_source"])
        if source == "database":
            credential = {"source": source, "configured": bool(row["credential_ciphertext"])}
        elif source == "environment":
            env_name = str(row["credential_env_var"] or "")
            credential = {"source": source, "configured": bool(env_name and os.environ.get(env_name)), "env_name": env_name}
        else:
            credential = {"source": "none", "configured": True}
        return {"id": str(row["id"]), "name": str(row["name"]), "provider": str(row["provider_preset"]), "base_url": str(version["base_url"] if version else ""), "timeout": float(version["timeout_seconds"] if version else 100), "enabled": bool(row["is_enabled"]), "credential": credential, "models": public_models, "version": int(version["version_number"] if version else 0)}

    def list_channels(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [self._public_channel(conn, row) for row in conn.execute("SELECT * FROM model_channels ORDER BY id")]

    def get_channel(self, channel_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM model_channels WHERE id=?", (channel_id,)).fetchone()
            return None if row is None else self._public_channel(conn, row)

    def _append_settings_version(self, conn: sqlite3.Connection, section: str, snapshot: Any) -> int:
        version = int(conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM app_setting_versions WHERE section=?", (section,)).fetchone()[0])
        conn.execute("INSERT INTO app_setting_versions(section,version,snapshot_json,created_at) VALUES(?,?,?,?)", (section, version, _dump(snapshot), _now()))
        return version

    def save_channel(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise SettingsError("通道名称不能为空")
        source_data = payload.get("credential") or {"source": "none"}
        source = str(source_data.get("source") or "none")
        if source not in {"none", "environment", "database"}:
            raise SettingsError("凭据来源无效")
        with self._connect() as conn:
            channel_id = payload.get("id")
            row = conn.execute("SELECT * FROM model_channels WHERE id=?", (channel_id,)).fetchone() if channel_id else None
            now = _now()
            if row is None:
                cursor = conn.execute("INSERT INTO model_channels(name,provider_preset,credential_source,credential_env_var,is_enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (name, str(payload.get("provider") or "custom"), source, str(source_data.get("env_name") or "") or None, 1 if payload.get("enabled", True) else 0, now, now))
                channel_id = str(cursor.lastrowid)
                row = conn.execute("SELECT * FROM model_channels WHERE id=?", (channel_id,)).fetchone()
            else:
                channel_id = str(row["id"])
            previous = self._public_channel(conn, row)
            cipher = row["credential_ciphertext"]
            if source == "database" and source_data.get("value"):
                cipher = self._encrypt(channel_id, str(source_data["value"]))
            elif source != "database":
                cipher = None
            if source == "database" and not cipher:
                raise SettingsError("数据库凭据不能为空")
            conn.execute("UPDATE model_channels SET name=?,provider_preset=?,credential_source=?,credential_ciphertext=?,credential_env_var=?,is_enabled=?,updated_at=? WHERE id=?", (name, str(payload.get("provider") or "custom"), source, cipher, str(source_data.get("env_name") or "") or None, 1 if payload.get("enabled", True) else 0, now, channel_id))
            version_number = int(conn.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM model_channel_versions WHERE channel_id=?", (channel_id,)).fetchone()[0])
            models = []
            default_model = None
            for value in payload.get("models") or []:
                model_id = str(value.get("model_id") or "").strip()
                if not model_id:
                    continue
                item = {"model_id": model_id, "name": str(value.get("name") or model_id), "enabled": bool(value.get("enabled", True)), "vision_capable": value.get("vision_capable")}
                models.append(item)
                if value.get("is_default") and default_model is None:
                    default_model = model_id
            incoming_ids = {item["model_id"] for item in models}
            for old_model in previous["models"]:
                old_model_id = old_model["model_id"]
                if old_model_id in incoming_ids:
                    continue
                if self._referenced(
                    conn, previous["name"], old_model_id
                ) or (
                    name != previous["name"]
                    and self._referenced(conn, name, old_model_id)
                ):
                    models.append(
                        {
                            "model_id": old_model_id,
                            "name": old_model["name"],
                            "enabled": False,
                            "vision_capable": old_model.get("vision_capable"),
                        }
                    )
            if models and default_model is None:
                default_model = models[0]["model_id"]
            cursor = conn.execute("INSERT INTO model_channel_versions(channel_id,version_number,base_url,models_json,default_model,timeout_seconds,created_at) VALUES(?,?,?,?,?,?,?)", (channel_id, version_number, str(payload.get("base_url") or "").strip().rstrip("/"), _dump(models), default_model, max(1, int(float(payload.get("timeout") or 100))), now))
            conn.execute("UPDATE model_channels SET current_version_id=? WHERE id=?", (cursor.lastrowid, channel_id))
            self._append_settings_version(conn, "model_channels", {"channel_id": channel_id, "channel_version": version_number})
            updated = conn.execute("SELECT * FROM model_channels WHERE id=?", (channel_id,)).fetchone()
            return self._public_channel(conn, updated)

    def resolve_credential(self, channel_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM model_channels WHERE id=?", (channel_id,)).fetchone()
            if row is None:
                raise KeyError(channel_id)
            if row["credential_source"] == "environment":
                return os.environ.get(str(row["credential_env_var"] or ""), "")
            if row["credential_source"] == "database" and row["credential_ciphertext"]:
                return self._decrypt(str(row["id"]), row["credential_ciphertext"])
            return ""

    @staticmethod
    def _score_columns(conn: sqlite3.Connection) -> set[str]:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(photo_scores)")}

    def _referenced(
        self,
        conn: sqlite3.Connection,
        channel_name: str,
        model_id: str | None = None,
    ) -> bool:
        columns = self._score_columns(conn)
        if "analysis_channel" not in columns:
            return False
        if model_id is not None and "analysis_model" in columns:
            return conn.execute(
                "SELECT 1 FROM photo_scores WHERE analysis_channel=? AND analysis_model=? LIMIT 1",
                (channel_name, model_id),
            ).fetchone() is not None
        return conn.execute(
            "SELECT 1 FROM photo_scores WHERE analysis_channel=? LIMIT 1",
            (channel_name,),
        ).fetchone() is not None

    def delete_channel(self, channel_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM model_channels WHERE id=?", (channel_id,)).fetchone()
            if row is None:
                raise KeyError(channel_id)
            if self._referenced(conn, str(row["name"])):
                conn.execute("UPDATE model_channels SET is_enabled=0,updated_at=? WHERE id=?", (_now(), channel_id))
                result = "disabled"
            else:
                conn.execute("DELETE FROM model_fallback_chain WHERE channel_id=?", (channel_id,))
                conn.execute("DELETE FROM model_channel_versions WHERE channel_id=?", (channel_id,))
                conn.execute("DELETE FROM model_channels WHERE id=?", (channel_id,))
                result = "deleted"
            self._append_settings_version(conn, "model_channels", {"channel_id": channel_id, "result": result})
        return {"result": result, "channel": self.get_channel(channel_id)}

    def save_fallback_chain(self, items: list[dict[str, str]]) -> dict[str, Any]:
        with self._connect() as conn:
            normalized = []
            for item in items:
                channel = self.get_channel(str(item.get("channel_id") or ""))
                model_id = str(item.get("model_id") or "")
                if not channel or not channel["enabled"] or model_id not in {m["model_id"] for m in channel["models"] if m["enabled"]}:
                    raise SettingsError("降级链包含不存在或已停用的通道模型")
                normalized.append({"channel_id": channel["id"], "model_id": model_id})
            conn.execute("DELETE FROM model_fallback_chain")
            conn.executemany("INSERT INTO model_fallback_chain(position,channel_id,model_id) VALUES(?,?,?)", [(index, item["channel_id"], item["model_id"]) for index, item in enumerate(normalized)])
            version = self._append_settings_version(conn, "fallback_chain", normalized)
        return {"version": version, "items": normalized}

    def get_fallback_chain(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            return [{"channel_id": str(row[0]), "model_id": str(row[1])} for row in conn.execute("SELECT channel_id,model_id FROM model_fallback_chain ORDER BY position")]

    def save_section(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        if section not in {"analysis_defaults", "scan_settings", "security_settings"}:
            raise SettingsError("不支持的设置区")
        with self._connect() as conn:
            version = self._append_settings_version(conn, section, payload)
            conn.execute("INSERT INTO app_settings(key,value_json,version,updated_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,version=excluded.version,updated_at=excluded.updated_at", (section, _dump(payload), version, _now()))
        return {"section": section, "version": version, "value": payload}

    def save_analysis_defaults(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.save_section("analysis_defaults", payload)

    def get_section(self, section: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM app_settings WHERE key=?", (section,)).fetchone()
        return {"section": section, "version": 0, "value": default or {}} if row is None else {"section": section, "version": int(row["version"]), "value": json.loads(row["value_json"])}

    def list_versions(self, section: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT version,snapshot_json,created_at FROM app_setting_versions WHERE section=? ORDER BY version DESC", (section,)).fetchall()
        return [{"version": int(row["version"]), "snapshot": json.loads(row["snapshot_json"]), "created_at": row["created_at"]} for row in rows]

    def runtime_channels(self) -> list[dict[str, Any]]:
        channels = {item["id"]: item for item in self.list_channels()}
        result = []
        for item in self.get_fallback_chain():
            channel = channels.get(item["channel_id"])
            enabled_models = {
                model["model_id"] for model in channel["models"] if model["enabled"]
            } if channel else set()
            if channel and channel["enabled"] and item["model_id"] in enabled_models:
                try:
                    api_key = self.resolve_credential(channel["id"])
                except Exception:
                    continue
                result.append({"name": channel["name"], "api_url": channel["base_url"].rstrip("/") + "/chat/completions", "api_key": api_key, "model_name": item["model_id"], "timeout": channel["timeout"]})
        return result
