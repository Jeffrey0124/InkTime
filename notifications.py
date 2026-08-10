#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Small persistence boundary for application notifications."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class NotificationStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def create_once(self, *, kind: str, title: str, message: str, target_url: str) -> None:
        conn = self._connect()
        try:
            exists = conn.execute(
                "SELECT 1 FROM notifications WHERE kind=? AND target_url=? LIMIT 1",
                (kind, target_url),
            ).fetchone()
            if exists is None:
                conn.execute(
                    "INSERT INTO notifications(kind, title, message, target_url, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (kind, title[:120], message[:500], target_url, _now()),
                )
                conn.commit()
        finally:
            conn.close()

    def list_recent(self, limit: int = 30) -> list[dict[str, object]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, kind, title, message, target_url, is_read, created_at "
                "FROM notifications ORDER BY is_read ASC, id DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def mark_read(self, notification_id: int) -> bool:
        conn = self._connect()
        try:
            cursor = conn.execute(
                "UPDATE notifications SET is_read=1, read_at=? WHERE id=?",
                (_now(), int(notification_id)),
            )
            conn.commit()
            return cursor.rowcount == 1
        finally:
            conn.close()
