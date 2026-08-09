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


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    if column not in _table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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


def _ensure_photo_tables(conn: sqlite3.Connection) -> None:
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
    _ensure_column(conn, "photos", "file_status", "TEXT NOT NULL DEFAULT 'present'")
    _ensure_column(
        conn,
        "photos",
        "analysis_status",
        "TEXT NOT NULL DEFAULT 'analyzed'",
    )
    _ensure_column(
        conn,
        "photos",
        "visibility_status",
        "TEXT NOT NULL DEFAULT 'active'",
    )
    _ensure_column(conn, "photos", "current_analysis_version_id", "INTEGER")
    _ensure_column(conn, "photos", "archived_at", "TEXT")
    _ensure_column(conn, "photos", "excluded_at", "TEXT")
    _ensure_column(conn, "photos", "filename", "TEXT")
    _ensure_column(conn, "photos", "relative_directory", "TEXT")
    _ensure_column(conn, "photos", "file_extension", "TEXT")
    _ensure_column(conn, "photos", "media_type", "TEXT")
    _ensure_column(conn, "photos", "width", "INTEGER")
    _ensure_column(conn, "photos", "height", "INTEGER")
    _ensure_column(conn, "photos", "captured_at", "TEXT")
    _ensure_column(conn, "photos", "gps_lat", "REAL")
    _ensure_column(conn, "photos", "gps_lon", "REAL")
    _ensure_column(conn, "photos", "gps_alt", "REAL")
    _ensure_column(conn, "photos", "unreadable_reason", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photos_file_hash ON photos(file_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photos_visibility ON photos(visibility_status, file_status)"
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


def _ensure_analysis_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          photo_id INTEGER NOT NULL,
          source_task_item_id INTEGER,
          version_number INTEGER NOT NULL,
          caption TEXT,
          side_caption TEXT,
          photo_type TEXT,
          memory_score REAL,
          beauty_score REAL,
          reason TEXT,
          crop_focus_json TEXT,
          analysis_channel TEXT,
          analysis_model TEXT,
          result_json TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(photo_id, version_number),
          FOREIGN KEY(photo_id) REFERENCES photos(id)
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS analysis_versions_immutable_update
        BEFORE UPDATE ON analysis_versions
        BEGIN
          SELECT RAISE(ABORT, 'analysis_versions are immutable');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS analysis_versions_immutable_delete
        BEFORE DELETE ON analysis_versions
        BEGIN
          SELECT RAISE(ABORT, 'analysis_versions are immutable');
        END
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          task_type TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued',
          queue_position INTEGER,
          concurrency INTEGER NOT NULL DEFAULT 1,
          model_strategy_json TEXT NOT NULL DEFAULT '{}',
          total_count INTEGER NOT NULL DEFAULT 0,
          processed_count INTEGER NOT NULL DEFAULT 0,
          succeeded_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS analysis_task_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          task_id INTEGER NOT NULL,
          photo_id INTEGER NOT NULL,
          position INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'queued',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          current_execution_level INTEGER,
          error_code TEXT,
          error_message TEXT,
          analysis_version_id INTEGER,
          started_at TEXT,
          finished_at TEXT,
          UNIQUE(task_id, photo_id),
          FOREIGN KEY(task_id) REFERENCES analysis_tasks(id),
          FOREIGN KEY(photo_id) REFERENCES photos(id),
          FOREIGN KEY(analysis_version_id) REFERENCES analysis_versions(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_tasks_status ON analysis_tasks(status, queue_position)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_task_items_task_status ON analysis_task_items(task_id, status)"
    )


def _ensure_scan_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scan_tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          status TEXT NOT NULL,
          root_path TEXT NOT NULL,
          trigger_sources_json TEXT NOT NULL DEFAULT '[]',
          discovered_count INTEGER NOT NULL DEFAULT 0,
          readable_count INTEGER NOT NULL DEFAULT 0,
          unreadable_count INTEGER NOT NULL DEFAULT 0,
          missing_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          error_message TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_tasks_one_active
        ON scan_tasks((1)) WHERE status IN ('queued', 'running')
        """
    )


def _ensure_model_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_channels (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL UNIQUE,
          provider_preset TEXT NOT NULL,
          credential_source TEXT NOT NULL DEFAULT 'none',
          credential_ciphertext TEXT,
          credential_env_var TEXT,
          is_enabled INTEGER NOT NULL DEFAULT 1,
          current_version_id INTEGER,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_channel_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          channel_id INTEGER NOT NULL,
          version_number INTEGER NOT NULL,
          base_url TEXT NOT NULL,
          models_json TEXT NOT NULL DEFAULT '[]',
          default_model TEXT,
          timeout_seconds INTEGER NOT NULL DEFAULT 100,
          created_at TEXT NOT NULL,
          UNIQUE(channel_id, version_number),
          FOREIGN KEY(channel_id) REFERENCES model_channels(id)
        )
        """
    )


def _ensure_notification_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          title TEXT NOT NULL,
          message TEXT NOT NULL,
          target_url TEXT,
          is_read INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          read_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(is_read, created_at)"
    )


def _ensure_tables(conn: sqlite3.Connection) -> None:
    _ensure_photo_tables(conn)
    _ensure_analysis_tables(conn)
    _ensure_scan_tables(conn)
    _ensure_model_tables(conn)
    _ensure_notification_tables(conn)


def _backfill_legacy_photo_states(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE photos
        SET file_status = 'missing'
        WHERE file_status = 'present'
          AND (exists_on_disk = 0 OR status = 'missing')
        """
    )
    if _table_exists(conn, "photo_scores"):
        conn.execute(
            """
            UPDATE photos
            SET analysis_status = 'pending'
            WHERE analysis_status = 'analyzed'
              AND current_analysis_version_id IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM photo_scores WHERE photo_scores.path = photos.path
              )
            """
        )
    else:
        conn.execute(
            """
            UPDATE photos
            SET analysis_status = 'pending'
            WHERE analysis_status = 'analyzed'
              AND current_analysis_version_id IS NULL
            """
        )


def _legacy_value(row: sqlite3.Row, column: str):
    return row[column] if column in row.keys() else None


def _backfill_analysis_versions(conn: sqlite3.Connection, now: str) -> None:
    if not _table_exists(conn, "photo_scores"):
        return

    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT photos.id AS photo_id, photos.current_analysis_version_id,
               photo_scores.*
        FROM photos
        JOIN photo_scores ON photo_scores.path = photos.path
        WHERE photos.current_analysis_version_id IS NULL
        ORDER BY photos.id
        """
    ).fetchall()
    for row in rows:
        existing = conn.execute(
            "SELECT id FROM analysis_versions WHERE photo_id = ? ORDER BY version_number DESC",
            (row["photo_id"],),
        ).fetchone()
        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO analysis_versions
                (photo_id, source_task_item_id, version_number, caption,
                 side_caption, photo_type, memory_score, beauty_score, reason,
                 crop_focus_json, analysis_channel, analysis_model, result_json,
                 created_at)
                VALUES (?, NULL, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["photo_id"],
                    _legacy_value(row, "caption"),
                    _legacy_value(row, "side_caption"),
                    _legacy_value(row, "type"),
                    _legacy_value(row, "memory_score"),
                    _legacy_value(row, "beauty_score"),
                    _legacy_value(row, "reason"),
                    _legacy_value(row, "crop_focus_json"),
                    _legacy_value(row, "analysis_channel"),
                    _legacy_value(row, "analysis_model"),
                    _legacy_value(row, "raw_json"),
                    now,
                ),
            )
            version_id = int(cursor.lastrowid)
        else:
            version_id = int(existing["id"])
        conn.execute(
            """
            UPDATE photos
            SET current_analysis_version_id = ?, analysis_status = 'analyzed'
            WHERE id = ?
            """,
            (version_id, row["photo_id"]),
        )


def ensure_photo_identity_schema(db_path: str | Path) -> PhotoIdentitySummary:
    """Upgrade the compatible photo schema and backfill legacy analysis data."""

    db = Path(db_path).expanduser()
    conn = sqlite3.connect(db)
    try:
        _ensure_tables(conn)
        _backfill_legacy_photo_states(conn)
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
            file_status = "present" if is_file else "missing"
            missing_at = None if is_file else now
            exists_on_disk = 1 if is_file else 0
            if not is_file:
                missing_count += 1

            existing = conn.execute(
                """
                SELECT id, size_bytes, mtime, exists_on_disk, status, missing_at,
                       file_status
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
                     file_status, analysis_status, visibility_status,
                     created_at, updated_at, missing_at)
                    VALUES (?, NULL, ?, ?, ?, ?, ?, 'analyzed', 'active', ?, ?, ?)
                    """,
                    (
                        raw_path,
                        size_bytes,
                        mtime,
                        exists_on_disk,
                        status,
                        file_status,
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
                or existing[6] != file_status
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
                    file_status = ?,
                    updated_at = ?,
                    missing_at = CASE
                        WHEN ? = 1 THEN NULL
                        WHEN missing_at IS NULL THEN ?
                        ELSE missing_at
                    END
                WHERE path = ?
                """,
                (
                    size_bytes,
                    mtime,
                    exists_on_disk,
                    status,
                    file_status,
                    now,
                    exists_on_disk,
                    now,
                    raw_path,
                ),
            )
            updated += 1

        _backfill_analysis_versions(conn, now)
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
