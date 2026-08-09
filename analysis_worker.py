#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Consume queued analysis tasks and persist immutable analysis versions."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from photo_identity import ensure_photo_identity_schema
from settings_store import SettingsStore


AnalysisExecutor = Callable[[Path, dict[str, Any]], dict[str, Any]]


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _ensure_photo_scores(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS photo_scores (
          path TEXT PRIMARY KEY, caption TEXT, type TEXT,
          memory_score REAL, beauty_score REAL, reason TEXT,
          width INTEGER, height INTEGER, orientation TEXT, used_at TEXT,
          exif_json TEXT, raw_json TEXT, exif_datetime TEXT, exif_make TEXT,
          exif_model TEXT, exif_iso INTEGER, exif_exposure_time REAL,
          exif_f_number REAL, exif_focal_length REAL, exif_gps_lat REAL,
          exif_gps_lon REAL, exif_gps_alt REAL, side_caption TEXT,
          exif_city TEXT, location_hint TEXT, analysis_channel TEXT,
          analysis_model TEXT, crop_focus_json TEXT
        )
        """
    )
    definitions = {
        "caption": "TEXT", "type": "TEXT", "memory_score": "REAL",
        "beauty_score": "REAL", "reason": "TEXT", "width": "INTEGER",
        "height": "INTEGER", "orientation": "TEXT", "used_at": "TEXT",
        "exif_json": "TEXT", "raw_json": "TEXT", "exif_datetime": "TEXT",
        "exif_make": "TEXT", "exif_model": "TEXT", "exif_iso": "INTEGER",
        "exif_exposure_time": "REAL", "exif_f_number": "REAL",
        "exif_focal_length": "REAL", "exif_gps_lat": "REAL",
        "exif_gps_lon": "REAL", "exif_gps_alt": "REAL",
        "side_caption": "TEXT", "exif_city": "TEXT", "location_hint": "TEXT",
        "analysis_channel": "TEXT", "analysis_model": "TEXT",
        "crop_focus_json": "TEXT",
    }
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(photo_scores)")}
    for name, definition in definitions.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE photo_scores ADD COLUMN {name} {definition}")


class LegacyAnalysisExecutor:
    """Adapter around the existing analysis implementation for one frozen level."""

    def __init__(self, settings_store: SettingsStore) -> None:
        self.settings_store = settings_store
        self._city_resolver = None

    def __call__(self, source: Path, level: dict[str, Any]) -> dict[str, Any]:
        import analyze_photos

        runtime = self.settings_store.resolve_runtime_channel(
            str(level["channel_id"]),
            int(level["channel_version"]),
            str(level["model_id"]),
        )
        analyze_photos.API_CHANNELS = [runtime]
        analyze_photos._channel_cooldown_until = [0.0]
        analyze_photos._channel_inflight = [0]
        analyze_photos.TIMEOUT = runtime["timeout"]
        if self._city_resolver is None:
            self._city_resolver = analyze_photos.get_city_resolver()
        result = analyze_photos._process_one_photo(source, self._city_resolver)
        if result is None:
            raise RuntimeError("模型分析未返回有效结果")
        return result


class AnalysisWorker:
    def __init__(self, db_path: str | Path, executor: AnalysisExecutor) -> None:
        self.db_path = Path(db_path)
        self.executor = executor
        ensure_photo_identity_schema(self.db_path)
        conn = self._connect()
        try:
            _ensure_photo_scores(conn)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _claim_task(self) -> sqlite3.Row | None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM analysis_tasks WHERE status='running' LIMIT 1"
            ).fetchone():
                conn.rollback()
                return None
            task = conn.execute(
                """
                SELECT * FROM analysis_tasks WHERE status='queued'
                ORDER BY COALESCE(queue_position, 2147483647), id LIMIT 1
                """
            ).fetchone()
            if task is None:
                conn.rollback()
                return None
            now = _now()
            conn.execute(
                """
                UPDATE analysis_tasks
                SET status='running', started_at=COALESCE(started_at, ?), updated_at=?
                WHERE id=? AND status='queued'
                """,
                (now, now, task["id"]),
            )
            conn.commit()
            return task
        finally:
            conn.close()

    def _next_item(self, task_id: int) -> sqlite3.Row | None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            item = conn.execute(
                """
                SELECT ati.*, p.path FROM analysis_task_items ati
                JOIN photos p ON p.id=ati.photo_id
                WHERE ati.task_id=? AND ati.status='queued'
                ORDER BY ati.position LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if item is None:
                conn.rollback()
                return None
            conn.execute(
                """
                UPDATE analysis_task_items
                SET status='running', attempt_count=attempt_count+1,
                    current_execution_level=0, started_at=?
                WHERE id=? AND status='queued'
                """,
                (_now(), item["id"]),
            )
            conn.commit()
            return item
        finally:
            conn.close()

    @staticmethod
    def _json_text(value: Any, default: str = "") -> str:
        if value in (None, ""):
            return default
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _save_success(
        self, task_id: int, item: sqlite3.Row, result: dict[str, Any], level: dict[str, Any]
    ) -> None:
        required = ("caption", "type", "memory_score", "beauty_score", "reason")
        if any(result.get(key) is None for key in required):
            raise ValueError("分析结果缺少必要字段")
        now = _now()
        path = str(item["path"])
        channel = str(result.get("analysis_channel") or level["channel_name"])
        model = str(result.get("analysis_model") or level["model_id"])
        crop_focus = self._json_text(
            result.get("crop_focus_json", result.get("crop_focus"))
        )
        result_json = self._json_text(result.get("raw_json"), self._json_text(result))
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            version_number = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM analysis_versions WHERE photo_id=?",
                    (item["photo_id"],),
                ).fetchone()[0]
            )
            cursor = conn.execute(
                """
                INSERT INTO analysis_versions
                (photo_id, source_task_item_id, version_number, caption, side_caption,
                 photo_type, memory_score, beauty_score, reason, crop_focus_json,
                 analysis_channel, analysis_model, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["photo_id"], item["id"], version_number,
                    str(result["caption"]), str(result.get("side_caption") or ""),
                    str(result["type"]), float(result["memory_score"]),
                    float(result["beauty_score"]), str(result["reason"]), crop_focus,
                    channel, model, result_json, now,
                ),
            )
            version_id = int(cursor.lastrowid)
            fields = (
                "caption", "type", "memory_score", "beauty_score", "reason",
                "width", "height", "orientation", "exif_json", "raw_json",
                "exif_datetime", "exif_make", "exif_model", "exif_iso",
                "exif_exposure_time", "exif_f_number", "exif_focal_length",
                "exif_gps_lat", "exif_gps_lon", "exif_gps_alt", "side_caption",
                "exif_city", "location_hint", "analysis_channel", "analysis_model",
                "crop_focus_json",
            )
            values = dict(result)
            values.update(
                analysis_channel=channel,
                analysis_model=model,
                crop_focus_json=crop_focus,
                raw_json=result_json,
            )
            columns = ", ".join(("path", *fields))
            placeholders = ", ".join("?" for _ in range(len(fields) + 1))
            updates = ", ".join(f"{field}=excluded.{field}" for field in fields)
            conn.execute(
                f"INSERT INTO photo_scores ({columns}) VALUES ({placeholders}) "
                f"ON CONFLICT(path) DO UPDATE SET {updates}",
                (path, *(values.get(field) for field in fields)),
            )
            conn.execute(
                """
                UPDATE photos SET analysis_status='analyzed',
                    current_analysis_version_id=?, updated_at=? WHERE id=?
                """,
                (version_id, now, item["photo_id"]),
            )
            conn.execute(
                """
                UPDATE analysis_task_items SET status='completed', analysis_version_id=?,
                    error_code=NULL, error_message=NULL, finished_at=? WHERE id=?
                """,
                (version_id, now, item["id"]),
            )
            self._refresh_counts(conn, task_id, now)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _save_failure(self, task_id: int, item_id: int, exc: Exception) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE analysis_task_items SET status='failed', error_code=?,
                    error_message=?, finished_at=? WHERE id=?
                """,
                (type(exc).__name__, str(exc)[:1000], now, item_id),
            )
            self._refresh_counts(conn, task_id, now)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _refresh_counts(conn: sqlite3.Connection, task_id: int, now: str) -> None:
        counts = conn.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN status IN ('completed','failed') THEN 1 ELSE 0 END) processed,
                   SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) succeeded,
                   SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed
            FROM analysis_task_items WHERE task_id=?
            """,
            (task_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE analysis_tasks SET total_count=?, processed_count=?,
                succeeded_count=?, failed_count=?, updated_at=? WHERE id=?
            """,
            tuple(int(counts[key] or 0) for key in ("total", "processed", "succeeded", "failed"))
            + (now, task_id),
        )

    def _finish_task(self, task_id: int) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            failed = int(
                conn.execute(
                    "SELECT COUNT(*) FROM analysis_task_items WHERE task_id=? AND status='failed'",
                    (task_id,),
                ).fetchone()[0]
            )
            status = "completed_with_failures" if failed else "completed"
            conn.execute(
                "UPDATE analysis_tasks SET status=?, finished_at=?, updated_at=? WHERE id=?",
                (status, now, now, task_id),
            )
            conn.execute("DELETE FROM analysis_task_occupancy WHERE task_id=?", (task_id,))
            conn.commit()
        finally:
            conn.close()

    def run_once(self) -> dict[str, Any] | None:
        task = self._claim_task()
        if task is None:
            return None
        task_id = int(task["id"])
        strategy = json.loads(task["model_strategy_json"])
        levels = strategy.get("execution_levels") or []
        if not levels:
            raise RuntimeError("任务没有冻结的模型执行层级")
        level = dict(levels[0])
        while (item := self._next_item(task_id)) is not None:
            try:
                result = self.executor(Path(str(item["path"])), level)
                self._save_success(task_id, item, result, level)
            except Exception as exc:
                self._save_failure(task_id, int(item["id"]), exc)
        self._finish_task(task_id)
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM analysis_tasks WHERE id=?", (task_id,)).fetchone()
        finally:
            conn.close()
        return {
            "task_id": task_id,
            "status": str(row["status"]),
            "total_count": int(row["total_count"]),
            "processed_count": int(row["processed_count"]),
            "succeeded_count": int(row["succeeded_count"]),
            "failed_count": int(row["failed_count"]),
            "remaining_count": max(
                0, int(row["total_count"]) - int(row["processed_count"])
            ),
        }

    def run_forever(self, poll_interval: float = 2.0) -> None:
        while True:
            if self.run_once() is None:
                time.sleep(max(0.1, poll_interval))


def main() -> None:
    parser = argparse.ArgumentParser(description="InkTime analysis worker")
    parser.add_argument("--once", action="store_true", help="处理一个排队任务后退出")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args()
    import config as cfg

    db_path = Path(cfg.DB_PATH)
    master_key = str(
        getattr(cfg, "SETTINGS_MASTER_KEY", "")
        or os.environ.get("INKTIME_SETTINGS_MASTER_KEY", "")
    )
    settings = SettingsStore(db_path, master_key=master_key)
    worker = AnalysisWorker(db_path, LegacyAnalysisExecutor(settings))
    if args.once:
        worker.run_once()
    else:
        worker.run_forever(args.poll_interval)


if __name__ == "__main__":
    main()
