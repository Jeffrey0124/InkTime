#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Consume queued analysis tasks and persist immutable analysis versions."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable

from photo_identity import ensure_photo_identity_schema
from photo_scores_schema import ensure_photo_scores_schema
from notifications import NotificationStore
from settings_store import SettingsStore


AnalysisExecutor = Callable[[Path, dict[str, Any]], dict[str, Any]]


class AnalysisExecutionError(RuntimeError):
    """A model execution error with a stable retry classification."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


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
        if self._city_resolver is None:
            self._city_resolver = analyze_photos.get_city_resolver()
        result = analyze_photos.process_one_photo(
            source, self._city_resolver, runtime_channel=runtime
        )
        if result is None:
            raise RuntimeError("模型分析未返回有效结果")
        return result


class AnalysisWorker:
    def __init__(self, db_path: str | Path, executor: AnalysisExecutor) -> None:
        self.db_path = Path(db_path)
        self.executor = executor
        self.notifications = NotificationStore(self.db_path)
        ensure_photo_identity_schema(self.db_path)
        conn = self._connect()
        try:
            ensure_photo_scores_schema(conn)
            conn.commit()
        finally:
            conn.close()
        self._recover_interrupted_tasks()

    def _recover_interrupted_tasks(self) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            tasks = conn.execute(
                "SELECT id FROM analysis_tasks WHERE status='running'"
            ).fetchall()
            for task in tasks:
                task_id = int(task["id"])
                recovery_row = conn.execute(
                    "SELECT recovery_failures FROM analysis_task_runtime WHERE task_id=?",
                    (task_id,),
                ).fetchone()
                failures = int(recovery_row["recovery_failures"]) if recovery_row else 0
                if failures >= 2:
                    conn.execute(
                        "UPDATE analysis_tasks SET status='paused', updated_at=? WHERE id=?",
                        (now, task_id),
                    )
                    conn.execute(
                        "INSERT INTO analysis_task_runtime(task_id, pause_reason, recovery_failures) "
                        "VALUES (?, ?, ?) ON CONFLICT(task_id) DO UPDATE SET "
                        "pause_reason=excluded.pause_reason, recovery_failures=excluded.recovery_failures",
                        (task_id, "Worker 连续恢复失败，已暂停队列", failures + 1),
                    )
                    continue
                conn.execute(
                    "UPDATE analysis_task_items SET status='queued', current_execution_level=NULL, "
                    "started_at=NULL WHERE task_id=? AND status='running' "
                    "AND analysis_version_id IS NULL",
                    (task_id,),
                )
                conn.execute(
                    "UPDATE analysis_tasks SET status='queued', updated_at=? WHERE id=?",
                    (now, task_id),
                )
                conn.execute(
                    "INSERT INTO analysis_task_runtime(task_id, recovery_failures) VALUES (?, 1) "
                    "ON CONFLICT(task_id) DO UPDATE SET "
                    "recovery_failures=analysis_task_runtime.recovery_failures + 1",
                    (task_id,),
                )
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
                "SELECT 1 FROM analysis_tasks "
                "WHERE status IN ('running','pausing','paused','stopping') LIMIT 1"
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

    def _apply_control_boundary(self, task_id: int) -> str | None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM analysis_tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return "failed"
            status = str(row["status"])
            if status == "pausing":
                conn.execute(
                    "UPDATE analysis_tasks SET status='paused', updated_at=? WHERE id=?",
                    (now, task_id),
                )
                conn.commit()
                return "paused"
            if status == "stopping":
                conn.execute(
                    "UPDATE analysis_task_items SET status='stopped', finished_at=? "
                    "WHERE task_id=? AND status='queued'",
                    (now, task_id),
                )
                self._refresh_counts(conn, task_id, now)
                conn.execute(
                    "UPDATE analysis_tasks SET status='stopped', finished_at=?, updated_at=? "
                    "WHERE id=?",
                    (now, now, task_id),
                )
                conn.execute(
                    "DELETE FROM analysis_task_occupancy WHERE task_id=?", (task_id,)
                )
                conn.commit()
                return "stopped"
            if status != "running":
                conn.rollback()
                return status
            conn.rollback()
            return None
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

    def _is_circuit_open(self, task_id: int, level_index: int) -> bool:
        conn = self._connect()
        try:
            return conn.execute("SELECT 1 FROM analysis_task_circuits WHERE task_id=? AND execution_level=?", (task_id, level_index)).fetchone() is not None
        finally:
            conn.close()

    def _open_circuit(self, task_id: int, level_index: int, reason: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO analysis_task_circuits"
                "(task_id, execution_level, reason, created_at) VALUES (?, ?, ?, ?)",
                (task_id, level_index, reason[:1000], _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def _set_item_level(self, item_id: int, level_index: int) -> None:
        conn = self._connect()
        try:
            conn.execute("UPDATE analysis_task_items SET current_execution_level=? WHERE id=?", (level_index, item_id))
            conn.commit()
        finally:
            conn.close()

    def _pause_unavailable(self, task_id: int, item_id: int, reason: str) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE analysis_task_items SET status='queued', current_execution_level=NULL, error_code='all_levels_unavailable', error_message=? WHERE id=? AND status='running'", (reason[:1000], item_id))
            conn.execute("UPDATE analysis_tasks SET status='paused', updated_at=? WHERE id=?", (now, task_id))
            conn.execute("INSERT INTO analysis_task_runtime(task_id, pause_reason) VALUES (?, ?) ON CONFLICT(task_id) DO UPDATE SET pause_reason=excluded.pause_reason", (task_id, reason[:1000]))
            conn.commit()
        finally:
            conn.close()
        self.notifications.create_once(
            kind="analysis_channels_paused",
            title="模型通道不可用，任务已暂停",
            message=f"任务 #{task_id} 已暂停，请检查模型凭据或通道后恢复。",
            target_url=f"/analysis-tasks/{task_id}",
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, AnalysisExecutionError):
            return exc.retryable
        message = str(exc).lower()
        deterministic_markers = ("401", "403", "unauthorized", "forbidden", "invalid api key", "model not found", "model_not_found")
        return not any(marker in message for marker in deterministic_markers)

    def _process_item(self, task_id: int, item: sqlite3.Row, levels: list[dict[str, Any]], rounds: int) -> str:
        attempted = False
        for _round in range(rounds):
            for level_index, level in enumerate(levels):
                if self._is_circuit_open(task_id, level_index):
                    continue
                attempted = True
                self._set_item_level(int(item["id"]), level_index)
                try:
                    result = self.executor(Path(str(item["path"])), level)
                    self._save_success(task_id, item, result, level)
                    return "completed"
                except Exception as exc:
                    if not self._is_retryable(exc):
                        self._open_circuit(task_id, level_index, str(exc))
                    last_error = exc
            if all(self._is_circuit_open(task_id, index) for index in range(len(levels))):
                self._pause_unavailable(task_id, int(item["id"]), "全部模型执行级不可用：请更新凭据后恢复任务")
                return "paused"
        if not attempted:
            self._pause_unavailable(task_id, int(item["id"]), "全部模型执行级不可用：请更新凭据后恢复任务")
            return "paused"
        self._save_failure(task_id, int(item["id"]), last_error)
        return "failed"

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
            conn.execute(
                "UPDATE analysis_task_runtime SET recovery_failures=0 WHERE task_id=?",
                (task_id,),
            )
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
        if status == "completed":
            self.notifications.create_once(
                kind="analysis_completed",
                title="分析任务已完成",
                message=f"任务 #{task_id} 的全部照片已分析完成。",
                target_url=f"/analysis-tasks/{task_id}",
            )
        else:
            self.notifications.create_once(
                kind="analysis_partial_failure",
                title="分析任务部分失败",
                message=f"任务 #{task_id} 已结束，存在 {failed} 张失败照片。",
                target_url=f"/analysis-tasks/{task_id}",
            )

    def _fail_task(self, task_id: int, exc: Exception) -> None:
        now = _now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE analysis_task_items
                SET status='failed', attempt_count=CASE
                      WHEN status='running' THEN attempt_count ELSE attempt_count + 1 END,
                    error_code=?, error_message=?, finished_at=?
                WHERE task_id=? AND status IN ('queued', 'running')
                """,
                (type(exc).__name__, str(exc)[:1000], now, task_id),
            )
            self._refresh_counts(conn, task_id, now)
            conn.execute(
                """
                UPDATE analysis_tasks SET status='failed',
                    finished_at=?, updated_at=? WHERE id=?
                """,
                (now, now, task_id),
            )
            conn.execute("DELETE FROM analysis_task_occupancy WHERE task_id=?", (task_id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self.notifications.create_once(
            kind="analysis_failed",
            title="分析任务已停止",
            message=f"任务 #{task_id} 因执行错误停止，可在任务中心重试失败项。",
            target_url=f"/analysis-tasks/{task_id}",
        )

    def run_once(self) -> dict[str, Any] | None:
        task = self._claim_task()
        if task is None:
            return None
        task_id = int(task["id"])
        try:
            strategy = json.loads(task["model_strategy_json"])
            levels = strategy.get("execution_levels") or []
            if not levels:
                raise RuntimeError("任务没有冻结的模型执行层级")
            levels = [dict(level) for level in levels]
            rounds = max(1, min(2, int(strategy.get("max_request_rounds") or 2)))
            concurrency = max(1, min(4, int(task["concurrency"])))
            while True:
                if self._apply_control_boundary(task_id) is not None:
                    break
                items = [item for _ in range(concurrency) if (item := self._next_item(task_id))]
                if not items:
                    self._finish_task(task_id)
                    break
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    outcomes = list(pool.map(lambda item: self._process_item(task_id, item, levels, rounds), items))
                if "paused" in outcomes:
                    break
        except Exception as exc:
            self._fail_task(task_id, exc)
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


class AnalysisWorkerRunner:
    """Run one AnalysisWorker in a stoppable daemon thread."""

    def __init__(self, worker: AnalysisWorker, poll_interval: float = 2.0) -> None:
        self.worker = worker
        self.poll_interval = max(0.05, float(poll_interval))
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="analysis-worker",
            daemon=True,
        )
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                processed = self.worker.run_once()
            except Exception as exc:
                print(f"[WARN] 分析 Worker 轮询失败：{exc}")
                processed = None
            if processed is None:
                self._stop.wait(self.poll_interval)

    def shutdown(self, wait: bool = False) -> None:
        if not self.running:
            return
        self.running = False
        self._stop.set()
        if wait and self._thread.is_alive():
            self._thread.join(timeout=5)


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
