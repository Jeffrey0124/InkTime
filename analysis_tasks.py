#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Analysis task selection previews and atomic task creation."""

from __future__ import annotations

import datetime as dt
import json
import random
import sqlite3
from pathlib import Path
from typing import Any

from photo_identity import ensure_photo_identity_schema
from settings_store import SettingsStore


UNFINISHED_TASK_STATUSES = ("queued", "running", "pausing", "paused", "stopping")


class AnalysisTaskError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_request", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


class AnalysisTaskService:
    def __init__(self, db_path: str | Path, settings_store: SettingsStore) -> None:
        self.db_path = Path(db_path)
        self.settings_store = settings_store
        ensure_photo_identity_schema(self.db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analysis_task_occupancy (
                  photo_id INTEGER PRIMARY KEY,
                  task_id INTEGER NOT NULL,
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(photo_id) REFERENCES photos(id),
                  FOREIGN KEY(task_id) REFERENCES analysis_tasks(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_analysis_task_occupancy_task "
                "ON analysis_task_occupancy(task_id)"
            )
            placeholders = ",".join("?" for _ in UNFINISHED_TASK_STATUSES)
            conn.execute(
                "DELETE FROM analysis_task_occupancy WHERE task_id NOT IN "
                f"(SELECT id FROM analysis_tasks WHERE status IN ({placeholders}))",
                UNFINISHED_TASK_STATUSES,
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO analysis_task_occupancy(photo_id, task_id, created_at)
                SELECT ati.photo_id, ati.task_id, COALESCE(t.created_at, ?)
                FROM analysis_task_items ati
                JOIN analysis_tasks t ON t.id=ati.task_id
                WHERE t.status IN ({statuses})
                ORDER BY COALESCE(t.queue_position, 2147483647), t.id, ati.position
                """.format(statuses=placeholders),
                (_now(), *UNFINISHED_TASK_STATUSES),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _filter_sql(filters: dict[str, Any]) -> tuple[list[str], list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        exact = {
            "file_status": "p.file_status",
            "analysis_status": "p.analysis_status",
        }
        for key, column in exact.items():
            value = str(filters.get(key) or "").strip()
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)
        captured_from = str(filters.get("captured_from") or "").strip()
        captured_to = str(filters.get("captured_to") or "").strip()
        if captured_from:
            clauses.append("p.captured_at >= ?")
            params.append(captured_from)
        if captured_to:
            clauses.append("p.captured_at < date(?, '+1 day')")
            params.append(captured_to)
        gps = filters.get("has_gps")
        if gps in {True, 1, "1", "true", "yes"}:
            clauses.append("p.gps_lat IS NOT NULL AND p.gps_lon IS NOT NULL")
        elif gps in {False, 0, "0", "false", "no"}:
            clauses.append("(p.gps_lat IS NULL OR p.gps_lon IS NULL)")
        file_type = str(filters.get("file_type") or "").strip().lower().lstrip(".")
        if file_type:
            clauses.append(
                "(LOWER(LTRIM(p.file_extension, '.')) = ? OR LOWER(p.media_type) = ?)"
            )
            params.extend([file_type, file_type])
        photo_type = str(filters.get("type") or filters.get("photo_type") or "").strip()
        if photo_type:
            clauses.append("COALESCE(av.photo_type, '') = ?")
            params.append(photo_type)
        for key, column in (("directory", "p.relative_directory"), ("filename", "p.filename")):
            value = str(filters.get(key) or "").strip()
            if value:
                clauses.append(f"{column} LIKE ?")
                params.append(f"%{value}%")
        return clauses, params

    @staticmethod
    def _eligibility_reason(row: sqlite3.Row, task_type: str) -> str | None:
        if row["file_status"] != "present" or not int(row["exists_on_disk"] or 0):
            return "file_unavailable"
        if row["visibility_status"] != "active":
            return "not_active"
        if task_type == "incremental" and row["analysis_status"] == "analyzed":
            return "already_analyzed"
        if task_type == "reanalysis" and row["analysis_status"] != "analyzed":
            return "not_analyzed"
        if row["occupied_task_id"] is not None:
            return "occupied"
        return None

    def _candidate_rows(self, conn: sqlite3.Connection, selection: dict[str, Any]) -> list[sqlite3.Row]:
        kind = str(selection.get("kind") or "manual")
        filters = dict(selection.get("filters") or {})
        clauses, params = self._filter_sql(filters)
        manual_ids: list[int] = []
        if kind == "manual":
            manual_ids = list(dict.fromkeys(int(value) for value in selection.get("photo_ids") or []))
            if not manual_ids:
                return []
            clauses.append(f"p.id IN ({','.join('?' for _ in manual_ids)})")
            params.extend(manual_ids)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = conn.execute(
            f"""
            SELECT p.id, p.filename, p.captured_at, p.created_at, p.size_bytes,
                   p.file_status, p.exists_on_disk, p.analysis_status,
                   p.visibility_status, o.task_id AS occupied_task_id
            FROM photos p
            LEFT JOIN analysis_versions av ON av.id = p.current_analysis_version_id
            LEFT JOIN analysis_task_occupancy o ON o.photo_id = p.id
            {where}
            """,
            params,
        ).fetchall()
        if kind == "manual":
            positions = {photo_id: index for index, photo_id in enumerate(manual_ids)}
            return sorted(rows, key=lambda row: positions[int(row["id"])])
        sort = str(selection.get("sort") or "created_at")
        reverse = str(selection.get("order") or "desc").lower() != "asc"
        if sort == "random":
            random.Random(str(selection.get("seed") or "")).shuffle(rows)
            return rows
        key_name = {
            "captured_at": "captured_at",
            "created_at": "created_at",
            "filename": "filename",
            "size": "size_bytes",
        }.get(sort, "created_at")
        return sorted(
            rows,
            key=lambda row: (row[key_name] is not None, row[key_name] or "", int(row["id"])),
            reverse=reverse,
        )

    def _defaults(self) -> dict[str, Any]:
        return dict(self.settings_store.get_section("analysis_defaults").get("value") or {})

    def preview_selection(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_type = str(payload.get("task_type") or "incremental")
        if task_type not in {"incremental", "reanalysis"}:
            raise AnalysisTaskError("分析模式无效")
        selection = dict(payload.get("selection") or {})
        kind = str(selection.get("kind") or "manual")
        if kind not in {"manual", "all", "top_n"}:
            raise AnalysisTaskError("选择方式无效")
        if kind == "top_n":
            try:
                if int(selection.get("limit")) <= 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise AnalysisTaskError("选择数量必须大于零") from exc
        if str(selection.get("sort") or "") == "random" and not selection.get("seed"):
            selection["seed"] = f"selection-{random.SystemRandom().getrandbits(64):016x}"

        conn = self._connect()
        try:
            rows = self._candidate_rows(conn, selection)
        finally:
            conn.close()
        eligible: list[int] = []
        reasons: dict[str, int] = {}
        for row in rows:
            reason = self._eligibility_reason(row, task_type)
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
            else:
                eligible.append(int(row["id"]))
        if kind == "top_n":
            eligible = eligible[: _positive_int(selection.get("limit"), len(eligible))]
        defaults = self._defaults()
        threshold = _positive_int(defaults.get("high_cost_threshold"), 50)
        return {
            "task_type": task_type,
            "kind": kind,
            "matched_count": len(rows),
            "eligible_count": len(rows) - sum(reasons.values()),
            "selected_count": len(eligible),
            "excluded_count": sum(reasons.values()),
            "excluded_reasons": reasons,
            "photo_ids": eligible,
            "seed": selection.get("seed"),
            "concurrency": min(4, _positive_int(defaults.get("concurrency"), 1)),
            "max_request_rounds": min(2, _positive_int(defaults.get("max_request_rounds"), 2)),
            "high_cost_threshold": threshold,
            "requires_high_cost_confirmation": task_type == "reanalysis" or len(eligible) > threshold,
            "execution_levels": self._strategy_snapshot()["execution_levels"],
        }

    def _strategy_snapshot(self) -> dict[str, Any]:
        channels = {channel["id"]: channel for channel in self.settings_store.list_channels()}
        levels = []
        for item in self.settings_store.get_fallback_chain():
            channel = channels.get(str(item["channel_id"]))
            if not channel or not channel["enabled"]:
                continue
            model_id = str(item["model_id"])
            if model_id not in {model["model_id"] for model in channel["models"] if model["enabled"]}:
                continue
            levels.append(
                {
                    "channel_id": channel["id"],
                    "channel_name": channel["name"],
                    "channel_version": channel["version"],
                    "model_id": model_id,
                }
            )
        if not levels:
            raise AnalysisTaskError("请先配置可用的模型降级链", code="model_strategy_empty")
        defaults = self._defaults()
        return {
            "execution_levels": levels,
            "max_request_rounds": min(2, _positive_int(defaults.get("max_request_rounds"), 2)),
        }

    def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        task_type = str(payload.get("task_type") or "incremental")
        if task_type not in {"incremental", "reanalysis"}:
            raise AnalysisTaskError("分析模式无效")
        photo_ids = list(dict.fromkeys(int(value) for value in payload.get("photo_ids") or []))
        if not photo_ids:
            raise AnalysisTaskError("没有可加入任务的素材")
        concurrency = _positive_int(payload.get("concurrency"), 1)
        if concurrency > 4:
            raise AnalysisTaskError("并发数必须在 1 到 4 之间")
        defaults = self._defaults()
        threshold = _positive_int(defaults.get("high_cost_threshold"), 50)
        if (task_type == "reanalysis" or len(photo_ids) > threshold) and not payload.get(
            "confirmed_high_cost"
        ):
            raise AnalysisTaskError("该任务需要二次确认", code="high_cost_confirmation_required", status=409)
        strategy = self._strategy_snapshot()
        now = _now()
        label = "重新分析" if task_type == "reanalysis" else "增量分析"
        name = str(payload.get("name") or "").strip() or f"{label} {now[:16].replace('T', ' ')}"

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in photo_ids)
            rows = conn.execute(
                f"""
                SELECT p.id, p.file_status, p.exists_on_disk, p.analysis_status,
                       p.visibility_status, o.task_id AS occupied_task_id
                FROM photos p
                LEFT JOIN analysis_task_occupancy o ON o.photo_id=p.id
                WHERE p.id IN ({placeholders})
                """,
                photo_ids,
            ).fetchall()
            by_id = {int(row["id"]): row for row in rows}
            valid = [
                photo_id
                for photo_id in photo_ids
                if photo_id in by_id and self._eligibility_reason(by_id[photo_id], task_type) is None
            ]
            if valid != photo_ids:
                raise AnalysisTaskError(
                    "素材选择已变化，请重新确认",
                    code="selection_changed",
                    status=409,
                )
            queue_position = int(
                conn.execute(
                    "SELECT COALESCE(MAX(queue_position), 0) + 1 FROM analysis_tasks "
                    f"WHERE status IN ({','.join('?' for _ in UNFINISHED_TASK_STATUSES)})",
                    UNFINISHED_TASK_STATUSES,
                ).fetchone()[0]
            )
            cursor = conn.execute(
                """
                INSERT INTO analysis_tasks
                (name, task_type, status, queue_position, concurrency,
                 model_strategy_json, total_count, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    task_type,
                    queue_position,
                    concurrency,
                    json.dumps(strategy, ensure_ascii=False, separators=(",", ":")),
                    len(photo_ids),
                    now,
                    now,
                ),
            )
            task_id = int(cursor.lastrowid)
            conn.executemany(
                "INSERT INTO analysis_task_items(task_id, photo_id, position, status) "
                "VALUES (?, ?, ?, 'queued')",
                [(task_id, photo_id, position) for position, photo_id in enumerate(photo_ids)],
            )
            conn.executemany(
                "INSERT INTO analysis_task_occupancy(photo_id, task_id, created_at) VALUES (?, ?, ?)",
                [(photo_id, task_id, now) for photo_id in photo_ids],
            )
            conn.commit()
        except AnalysisTaskError:
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise AnalysisTaskError(
                "素材选择已变化，请重新确认", code="selection_changed", status=409
            ) from exc
        finally:
            conn.close()
        return {
            "task_id": task_id,
            "name": name,
            "task_type": task_type,
            "status": "queued",
            "queue_position": queue_position,
            "concurrency": concurrency,
            "total_count": len(photo_ids),
        }

    def get_task(self, task_id: int) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT id, name, task_type, status, queue_position, concurrency,
                       model_strategy_json, total_count, processed_count,
                       succeeded_count, failed_count, created_at
                FROM analysis_tasks WHERE id=?
                """,
                (task_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {
            "task_id": int(row["id"]),
            "name": row["name"],
            "task_type": row["task_type"],
            "status": row["status"],
            "queue_position": row["queue_position"],
            "concurrency": int(row["concurrency"]),
            "strategy": json.loads(row["model_strategy_json"]),
            "total_count": int(row["total_count"]),
            "processed_count": int(row["processed_count"]),
            "succeeded_count": int(row["succeeded_count"]),
            "failed_count": int(row["failed_count"]),
            "created_at": row["created_at"],
        }
