#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""WebUI 所需的数据库读取和状态汇总。"""

from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path
from typing import Any

from render_photopainter import _extract_exif_date, _safe_display_caption


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".webp",
    ".heic",
    ".heif",
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _optional_column(columns: set[str], name: str, fallback: str = "''") -> str:
    return name if name in columns else f"{fallback} AS {name}"


def _score(memory_score: Any, beauty_score: Any) -> float:
    try:
        memory = float(memory_score or 0)
    except (TypeError, ValueError):
        memory = 0.0
    try:
        beauty = float(beauty_score or 0)
    except (TypeError, ValueError):
        beauty = 0.0
    return memory + beauty


def _count_monitor_files(monitor_dir: Path) -> int:
    if not monitor_dir.exists() or not monitor_dir.is_dir():
        return 0
    return sum(
        1
        for path in monitor_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def _load_push_manifest(push_dir: Path) -> dict[str, Any]:
    manifest_path = push_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _photo_id_for_path(db_path: Path, source_path: str) -> int | None:
    if not source_path:
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "photos"):
            return None
        row = conn.execute(
            """
            SELECT id FROM photos
            WHERE path = ?
            LIMIT 1
            """,
            (source_path,),
        ).fetchone()
        return int(row["id"]) if row is not None else None
    finally:
        conn.close()


def load_status(db_path: Path, *, monitor_dir: Path, push_dir: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        has_photo_scores = _table_exists(conn, "photo_scores")
        has_photos = _table_exists(conn, "photos")
        has_push_history = _table_exists(conn, "push_history")

        analyzed = 0
        if has_photo_scores:
            analyzed = int(conn.execute("SELECT COUNT(*) FROM photo_scores").fetchone()[0])

        missing = 0
        tracked = 0
        if has_photos:
            tracked = int(conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
            missing = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM photos
                    WHERE exists_on_disk = 0 OR status = 'missing'
                    """
                ).fetchone()[0]
            )

        recent_push: dict[str, Any] | None = None
        if has_push_history:
            row = conn.execute(
                """
                SELECT source_path, render_path, pushed_at, trigger_type, slot, exif_date, note
                FROM push_history
                ORDER BY pushed_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            if row is not None:
                recent_push = dict(row)
    finally:
        conn.close()

    manifest = _load_push_manifest(push_dir)
    if recent_push is None and manifest:
        recent_push = {
            "source_path": manifest.get("source_path", ""),
            "render_path": "",
            "pushed_at": manifest.get("published_at", ""),
            "trigger_type": manifest.get("trigger_type", ""),
            "slot": manifest.get("slot", ""),
            "exif_date": manifest.get("exif_date", ""),
            "note": "",
        }
    if recent_push is not None:
        source_path = str(recent_push.get("source_path") or "")
        recent_push.update(
            {
                "image_url": manifest.get("image_url", "/push/latest.bmp") if manifest else "",
                "preview_url": manifest.get("preview_url", "/push/latest.png") if manifest else "",
                "side_caption": manifest.get("side_caption", "") if manifest else "",
                "photo_id": _photo_id_for_path(db_path, source_path),
            }
        )

    monitored_files = _count_monitor_files(monitor_dir)
    return {
        "ok": True,
        "monitor_dir": str(monitor_dir),
        "monitor_dir_exists": monitor_dir.exists() and monitor_dir.is_dir(),
        "monitored_files": monitored_files,
        "tracked_photos": tracked,
        "analyzed_photos": analyzed,
        "missing_photos": missing,
        "unanalyzed_estimate": max(0, monitored_files - analyzed),
        "recent_push": recent_push,
        "health": {
            "database_exists": db_path.exists(),
            "push_manifest_exists": bool(manifest),
        },
    }


def load_photos(
    db_path: Path,
    *,
    limit: int = 60,
    sort: str = "score",
    include_missing: bool = False,
    random_seed: str | None = None,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "photo_scores") or not _table_exists(conn, "photos"):
            return []

        score_columns = _columns(conn, "photo_scores")
        side_caption_expr = _optional_column(score_columns, "side_caption")
        exif_city_expr = _optional_column(score_columns, "exif_city")
        location_hint_expr = _optional_column(score_columns, "location_hint")
        analysis_channel_expr = _optional_column(score_columns, "analysis_channel")
        analysis_model_expr = _optional_column(score_columns, "analysis_model")
        crop_focus_expr = _optional_column(score_columns, "crop_focus_json")
        exif_json_expr = _optional_column(score_columns, "exif_json")

        rows = conn.execute(
            f"""
            SELECT p.id AS photo_id,
                   p.path,
                   p.exists_on_disk,
                   p.status,
                   p.mtime,
                   s.caption,
                   s.type,
                   s.memory_score,
                   s.beauty_score,
                   s.reason,
                   {exif_json_expr},
                   {side_caption_expr},
                   {exif_city_expr},
                   {location_hint_expr},
                   {analysis_channel_expr},
                   {analysis_model_expr},
                   {crop_focus_expr},
                   o.custom_side_caption,
                   o.manual_crop_json,
                   o.render_overrides_json,
                   ra.preview_png_path,
                   ra.bmp_path,
                   ra.last_used_at
            FROM photos p
            JOIN photo_scores s ON s.path = p.path
            LEFT JOIN photo_overrides o ON o.photo_id = p.id
            LEFT JOIN (
              SELECT photo_id,
                     preview_png_path,
                     bmp_path,
                     MAX(last_used_at) AS last_used_at
              FROM render_assets
              GROUP BY photo_id
            ) ra ON ra.photo_id = p.id
            """
        ).fetchall()
    finally:
        conn.close()

    photos: list[dict[str, Any]] = []
    for row in rows:
        if not include_missing and (int(row["exists_on_disk"]) == 0 or row["status"] == "missing"):
            continue
        caption = str(row["caption"] or "")
        ptype = str(row["type"] or "")
        ai_side_caption = _safe_display_caption(str(row["side_caption"] or ""), caption, ptype)
        final_caption = str(row["custom_side_caption"] or ai_side_caption)
        combined_score = _score(row["memory_score"], row["beauty_score"])
        photos.append(
            {
                "photo_id": int(row["photo_id"]),
                "path": str(row["path"] or ""),
                "source_url": f"/api/photos/{int(row['photo_id'])}/source",
                "caption": caption,
                "side_caption": final_caption,
                "custom_side_caption": row["custom_side_caption"] or "",
                "ai_side_caption": ai_side_caption,
                "type": ptype,
                "memory_score": row["memory_score"],
                "beauty_score": row["beauty_score"],
                "score": combined_score,
                "reason": row["reason"] or "",
                "exif_date": _extract_exif_date(row["exif_json"]),
                "exif_city": row["exif_city"] or row["location_hint"] or "",
                "analysis_channel": row["analysis_channel"] or "",
                "analysis_model": row["analysis_model"] or "",
                "crop_focus_json": row["crop_focus_json"] or "",
                "manual_crop_json": row["manual_crop_json"] or "",
                "render_overrides_json": row["render_overrides_json"] or "",
                "exists_on_disk": bool(row["exists_on_disk"]),
                "status": row["status"],
                "last_rendered_at": row["last_used_at"] or "",
                "preview_png_path": row["preview_png_path"] or "",
                "bmp_path": row["bmp_path"] or "",
            }
        )

    if sort in {"random", "discovery", "push_rule"}:
        photos = [item for item in photos if float(item["score"]) >= 60]
        rng = random.Random(random_seed)
        rng.shuffle(photos)
    elif sort == "date":
        photos.sort(key=lambda item: (item["exif_date"] or "", item["path"]), reverse=True)
    elif sort == "rendered":
        photos.sort(key=lambda item: (item["last_rendered_at"] or "", item["path"]), reverse=True)
    else:
        photos.sort(key=lambda item: (float(item["score"]), item["path"]), reverse=True)

    return photos[:limit]


def load_photo(db_path: Path, photo_id: int) -> dict[str, Any] | None:
    photos = load_photos(db_path, limit=100000, include_missing=True)
    for photo in photos:
        if photo["photo_id"] == photo_id:
            return photo
    return None
