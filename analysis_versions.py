#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Public operations over immutable photo analysis versions."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path
from typing import Any


VERSION_FIELDS = (
    "caption",
    "side_caption",
    "photo_type",
    "memory_score",
    "beauty_score",
    "reason",
    "analysis_channel",
    "analysis_model",
    "created_at",
)


class AnalysisVersionError(ValueError):
    pass


class AnalysisVersionService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _version(self, conn: sqlite3.Connection, photo_id: int, version_id: int) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM analysis_versions WHERE id=? AND photo_id=?",
            (int(version_id), int(photo_id)),
        ).fetchone()
        if row is None:
            raise AnalysisVersionError("分析版本不存在")
        return row

    def list(self, photo_id: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, version_number, caption, side_caption, photo_type, memory_score, "
                "beauty_score, reason, analysis_channel, analysis_model, created_at "
                "FROM analysis_versions WHERE photo_id=? ORDER BY version_number DESC",
                (int(photo_id),),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def compare(self, photo_id: int, left_version_id: int, right_version_id: int) -> dict[str, dict[str, Any]]:
        conn = self._connect()
        try:
            left = self._version(conn, photo_id, left_version_id)
            right = self._version(conn, photo_id, right_version_id)
            return {field: {"left": left[field], "right": right[field]} for field in VERSION_FIELDS}
        finally:
            conn.close()

    def restore(self, photo_id: int, version_id: int) -> dict[str, int]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._version(conn, photo_id, version_id)
            cursor = conn.execute(
                "UPDATE photos SET current_analysis_version_id=?, analysis_status='analyzed', updated_at=? WHERE id=?",
                (int(version_id), dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), int(photo_id)),
            )
            if cursor.rowcount != 1:
                raise AnalysisVersionError("照片不存在")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"photo_id": int(photo_id), "current_version_id": int(version_id)}
