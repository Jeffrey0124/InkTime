#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""数据库照片身份层的渐进式迁移工具。"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PhotoIdentitySummary:
    inserted: int
    updated: int
    missing: int
    total: int


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _photo_scores_paths(conn: sqlite3.Connection) -> list[str]:
    if not _table_exists(conn, "photo_scores"):
        return []
    rows = conn.execute(
        """
        SELECT path FROM photo_scores
        WHERE path IS NOT NULL AND TRIM(path) != ''
        ORDER BY path
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS photos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          path TEXT NOT NULL UNIQUE,
          file_hash TEXT,
          size_bytes INTEGER,
          mtime REAL,
          exists_on_disk INTEGER NOT NULL DEFAULT 1,
          status TEXT NOT NULL DEFAULT 'analyzed',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          missing_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS photo_overrides (
          photo_id INTEGER PRIMARY KEY,
          custom_side_caption TEXT,
          manual_crop_json TEXT,
          render_overrides_json TEXT,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(photo_id) REFERENCES photos(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS render_assets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          photo_id INTEGER NOT NULL,
          variant_hash TEXT NOT NULL,
          preview_png_path TEXT NOT NULL,
          bmp_path TEXT,
          render_params_json TEXT NOT NULL,
          caption_used TEXT,
          created_at TEXT NOT NULL,
          last_used_at TEXT NOT NULL,
          UNIQUE(photo_id, variant_hash),
          FOREIGN KEY(photo_id) REFERENCES photos(id)
        )
        """
    )


def ensure_photo_identity_schema(db_path: str | Path) -> PhotoIdentitySummary:
    """Create the first-phase photo identity tables and backfill from photo_scores."""

    db = Path(db_path).expanduser()
    conn = sqlite3.connect(db)
    try:
        _ensure_tables(conn)
        inserted = 0
        updated = 0
        missing_count = 0
        now = _utc_now()

        for raw_path in _photo_scores_paths(conn):
            source = Path(raw_path).expanduser()
            is_file = source.is_file()
            size_bytes = source.stat().st_size if is_file else None
            mtime = source.stat().st_mtime if is_file else None
            status = "analyzed" if is_file else "missing"
            missing_at = None if is_file else now
            exists_on_disk = 1 if is_file else 0
            if not is_file:
                missing_count += 1

            existing = conn.execute(
                """
                SELECT id, size_bytes, mtime, exists_on_disk, status, missing_at
                FROM photos
                WHERE path = ?
                """,
                (raw_path,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO photos
                    (path, file_hash, size_bytes, mtime, exists_on_disk, status,
                     created_at, updated_at, missing_at)
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        raw_path,
                        size_bytes,
                        mtime,
                        exists_on_disk,
                        status,
                        now,
                        now,
                        missing_at,
                    ),
                )
                inserted += 1
                continue

            should_update = (
                existing[1] != size_bytes
                or existing[2] != mtime
                or existing[3] != exists_on_disk
                or existing[4] != status
                or (exists_on_disk == 1 and existing[5] is not None)
                or (exists_on_disk == 0 and existing[5] is None)
            )
            if not should_update:
                continue

            conn.execute(
                """
                UPDATE photos
                SET size_bytes = ?,
                    mtime = ?,
                    exists_on_disk = ?,
                    status = ?,
                    updated_at = ?,
                    missing_at = CASE
                        WHEN ? = 1 THEN NULL
                        WHEN missing_at IS NULL THEN ?
                        ELSE missing_at
                    END
                WHERE path = ?
                """,
                (size_bytes, mtime, exists_on_disk, status, now, exists_on_disk, now, raw_path),
            )
            updated += 1

        total = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        conn.commit()
        return PhotoIdentitySummary(
            inserted=inserted,
            updated=updated,
            missing=missing_count,
            total=int(total),
        )
    finally:
        conn.close()
