#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Single-administrator authentication for the local WebUI."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from flask import session


PASSWORD_ITERATIONS = 600_000
MIN_PASSWORD_LENGTH = 8
IDLE_TIMEOUT_SECONDS = 2 * 60 * 60
ABSOLUTE_TIMEOUT_SECONDS = 24 * 60 * 60


@dataclass
class LoginAttempt:
    failures: int = 0
    blocked_until: float = 0.0


class WebAuth:
    def __init__(
        self,
        db_path: str | Path,
        *,
        initial_password: str = "",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._attempts: dict[str, LoginAttempt] = {}
        self.ensure_schema()
        self.bootstrap(initial_password)

    def _timestamp(self) -> float:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_account (
                  id INTEGER PRIMARY KEY CHECK(id = 1),
                  password_salt BLOB NOT NULL,
                  password_hash BLOB NOT NULL,
                  password_iterations INTEGER NOT NULL,
                  session_generation INTEGER NOT NULL DEFAULT 1,
                  must_change_password INTEGER NOT NULL DEFAULT 1,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS web_auth_config (
                  id INTEGER PRIMARY KEY CHECK(id = 1),
                  session_secret TEXT NOT NULL,
                  created_at REAL NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def bootstrap(self, initial_password: str) -> None:
        conn = self._connect()
        try:
            exists = conn.execute("SELECT 1 FROM admin_account WHERE id = 1").fetchone()
            if exists is not None or len(initial_password) < MIN_PASSWORD_LENGTH:
                return
            salt, digest = self._hash_password(initial_password)
            now = self._timestamp()
            conn.execute(
                """
                INSERT INTO admin_account
                (id, password_salt, password_hash, password_iterations,
                 session_generation, must_change_password, created_at, updated_at)
                VALUES (1, ?, ?, ?, 1, 1, ?, ?)
                """,
                (salt, digest, PASSWORD_ITERATIONS, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _hash_password(password: str) -> tuple[bytes, bytes]:
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
        )
        return salt, digest

    @staticmethod
    def _verify_password(password: str, account: sqlite3.Row) -> bool:
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes(account["password_salt"]),
            int(account["password_iterations"]),
        )
        return secrets.compare_digest(candidate, bytes(account["password_hash"]))

    def _account(self) -> sqlite3.Row | None:
        conn = self._connect()
        try:
            return conn.execute("SELECT * FROM admin_account WHERE id = 1").fetchone()
        finally:
            conn.close()

    def is_configured(self) -> bool:
        return self._account() is not None

    def persistent_session_secret(self) -> str:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT session_secret FROM web_auth_config WHERE id = 1"
            ).fetchone()
            if row is not None:
                return str(row[0])
            generated = secrets.token_urlsafe(48)
            conn.execute(
                """
                INSERT OR IGNORE INTO web_auth_config (id, session_secret, created_at)
                VALUES (1, ?, ?)
                """,
                (generated, self._timestamp()),
            )
            conn.commit()
            row = conn.execute(
                "SELECT session_secret FROM web_auth_config WHERE id = 1"
            ).fetchone()
            return str(row[0])
        finally:
            conn.close()

    @staticmethod
    def csrf_token() -> str:
        token = str(session.get("csrf_token") or "")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    @staticmethod
    def clear_session() -> None:
        csrf = str(session.get("csrf_token") or "")
        session.clear()
        if csrf:
            session["csrf_token"] = csrf

    def session_state(self, *, touch: bool = True) -> dict[str, bool]:
        self.csrf_token()
        if not session.get("admin_authenticated"):
            return {"authenticated": False, "must_change_password": False}

        account = self._account()
        now = self._timestamp()
        created = float(session.get("auth_created_at") or 0)
        last_seen = float(session.get("auth_last_seen") or 0)
        generation = int(session.get("auth_generation") or 0)
        expired = (
            account is None
            or generation != int(account["session_generation"])
            or now - last_seen > IDLE_TIMEOUT_SECONDS
            or now - created > ABSOLUTE_TIMEOUT_SECONDS
        )
        if expired:
            self.clear_session()
            return {"authenticated": False, "must_change_password": False}

        if touch:
            session["auth_last_seen"] = now
        return {
            "authenticated": True,
            "must_change_password": bool(account["must_change_password"]),
        }

    def login(self, password: str, remote_key: str) -> tuple[dict[str, object], int]:
        now = self._timestamp()
        attempt = self._attempts.setdefault(remote_key, LoginAttempt())
        if attempt.blocked_until > now:
            return {
                "ok": False,
                "error": "login_delayed",
                "retry_after": max(1, int(attempt.blocked_until - now + 0.999)),
            }, 429

        account = self._account()
        if account is None:
            return {"ok": False, "error": "admin_not_configured"}, 503
        if not self._verify_password(password, account):
            attempt.failures += 1
            delay = min(60, 2 ** (attempt.failures - 1))
            attempt.blocked_until = now + delay
            return {"ok": False, "error": "invalid_credentials"}, 401

        self._attempts.pop(remote_key, None)
        csrf = self.csrf_token()
        session.clear()
        session.update(
            {
                "csrf_token": csrf,
                "admin_authenticated": True,
                "auth_generation": int(account["session_generation"]),
                "auth_created_at": now,
                "auth_last_seen": now,
            }
        )
        return {
            "ok": True,
            "authenticated": True,
            "must_change_password": bool(account["must_change_password"]),
        }, 200

    def change_password(self, current_password: str, new_password: str) -> tuple[dict, int]:
        state = self.session_state()
        if not state["authenticated"]:
            return {"ok": False, "error": "authentication_required"}, 401
        if len(new_password) < MIN_PASSWORD_LENGTH:
            return {"ok": False, "error": "password_too_short"}, 400

        account = self._account()
        if account is None or not self._verify_password(current_password, account):
            return {"ok": False, "error": "invalid_current_password"}, 401
        if secrets.compare_digest(current_password, new_password):
            return {"ok": False, "error": "password_unchanged"}, 400
        salt, digest = self._hash_password(new_password)
        conn = self._connect()
        try:
            updated = conn.execute(
                """
                UPDATE admin_account
                SET password_salt = ?, password_hash = ?, password_iterations = ?,
                    session_generation = session_generation + 1,
                    must_change_password = 0, updated_at = ?
                WHERE id = 1
                RETURNING session_generation
                """,
                (salt, digest, PASSWORD_ITERATIONS, self._timestamp()),
            ).fetchone()
            conn.commit()
        finally:
            conn.close()
        generation = int(updated[0])
        session["auth_generation"] = generation
        return {"ok": True, "must_change_password": False}, 200

    def reset_password(self, new_password: str) -> None:
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise ValueError("密码至少需要 8 个字符")
        salt, digest = self._hash_password(new_password)
        conn = self._connect()
        try:
            now = self._timestamp()
            conn.execute(
                """
                INSERT INTO admin_account
                (id, password_salt, password_hash, password_iterations,
                 session_generation, must_change_password, created_at, updated_at)
                VALUES (1, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  password_salt = excluded.password_salt,
                  password_hash = excluded.password_hash,
                  password_iterations = excluded.password_iterations,
                  session_generation = admin_account.session_generation + 1,
                  must_change_password = 1,
                  updated_at = excluded.updated_at
                """,
                (salt, digest, PASSWORD_ITERATIONS, 1, now, now),
            )
            conn.commit()
        finally:
            conn.close()
