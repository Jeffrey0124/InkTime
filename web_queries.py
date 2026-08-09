#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""WebUI 所需的数据库读取和状态汇总。"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from photo_metadata import coordinate_label, read_location_from_source
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


def _parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _is_anniversary(exif_date: Any, today: date) -> bool:
    captured = _parse_iso_date(exif_date)
    return captured is not None and (captured.month, captured.day) == (today.month, today.day)


def _is_not_recent(last_rendered_at: Any, cutoff: date) -> bool:
    last_rendered = _parse_iso_date(last_rendered_at)
    return last_rendered is None or last_rendered < cutoff


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
    exclude_days: int = 90,
    today: date | None = None,
    photo_id: int | None = None,
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
        exif_gps_lat_expr = _optional_column(score_columns, "exif_gps_lat", "NULL")
        exif_gps_lon_expr = _optional_column(score_columns, "exif_gps_lon", "NULL")
        exif_gps_alt_expr = _optional_column(score_columns, "exif_gps_alt", "NULL")
        where_clause = "WHERE p.id = ?" if photo_id is not None else ""
        query_params = (photo_id,) if photo_id is not None else ()
        has_push_history = _table_exists(conn, "push_history")
        push_history_join = """
            LEFT JOIN (
              SELECT source_path, MAX(pushed_at) AS last_pushed_at
              FROM push_history
              GROUP BY source_path
            ) ph ON ph.source_path = p.path
        """ if has_push_history else ""
        last_rendered_expr = (
            "COALESCE(ph.last_pushed_at, '') AS last_rendered_at"
            if has_push_history
            else "COALESCE(ra.last_used_at, '') AS last_rendered_at"
        )

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
                   {exif_gps_lat_expr},
                   {exif_gps_lon_expr},
                   {exif_gps_alt_expr},
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
                   {last_rendered_expr}
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
            {push_history_join}
            {where_clause}
            """
            , query_params
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
        stored_location = row["exif_city"] or row["location_hint"] or ""
        if not stored_location:
            stored_location = coordinate_label(row["exif_gps_lat"], row["exif_gps_lon"])
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
                "exif_json": row["exif_json"] or "",
                "exif_date": _extract_exif_date(row["exif_json"]),
                "exif_city": stored_location,
                "exif_gps_lat": row["exif_gps_lat"],
                "exif_gps_lon": row["exif_gps_lon"],
                "exif_gps_alt": row["exif_gps_alt"],
                "analysis_channel": row["analysis_channel"] or "",
                "analysis_model": row["analysis_model"] or "",
                "crop_focus_json": row["crop_focus_json"] or "",
                "manual_crop_json": row["manual_crop_json"] or "",
                "render_overrides_json": row["render_overrides_json"] or "",
                "exists_on_disk": bool(row["exists_on_disk"]),
                "status": row["status"],
                "last_rendered_at": row["last_rendered_at"] or "",
                "preview_png_path": row["preview_png_path"] or "",
                "bmp_path": row["bmp_path"] or "",
            }
        )

    if sort in {"random", "discovery", "push_rule"}:
        photos = [item for item in photos if float(item["score"]) >= 60]
        active_today = today or datetime.now().astimezone().date()
        cutoff = active_today - timedelta(days=max(0, exclude_days))
        anniversary_tier: list[dict[str, Any]] = []
        not_recent_tier: list[dict[str, Any]] = []
        fallback_tier: list[dict[str, Any]] = []
        for item in photos:
            if _is_anniversary(item["exif_date"], active_today):
                anniversary_tier.append(item)
            elif _is_not_recent(item["last_rendered_at"], cutoff):
                not_recent_tier.append(item)
            else:
                fallback_tier.append(item)
        rng = random.Random(random_seed)
        tiers = (anniversary_tier, not_recent_tier, fallback_tier)
        for tier in tiers:
            rng.shuffle(tier)
        photos = [item for tier in tiers for item in tier]
    elif sort == "date":
        photos.sort(key=lambda item: (item["exif_date"] or "", item["path"]), reverse=True)
    elif sort == "rendered":
        photos.sort(key=lambda item: (item["last_rendered_at"] or "", item["path"]), reverse=True)
    else:
        photos.sort(key=lambda item: (float(item["score"]), item["path"]), reverse=True)

    selected = photos[:limit]
    _hydrate_missing_locations(selected)
    return selected


def _hydrate_missing_locations(photos: list[dict[str, Any]]) -> None:
    candidates = [
        photo
        for photo in photos
        if not photo.get("exif_city") and photo.get("exists_on_disk") and photo.get("path")
    ]
    if not candidates:
        return

    for photo in candidates:
        metadata = read_location_from_source(str(photo["path"]))
        if not metadata.get("display"):
            continue
        photo["exif_city"] = str(metadata["display"])
        photo["exif_gps_lat"] = metadata.get("lat")
        photo["exif_gps_lon"] = metadata.get("lon")
        photo["exif_gps_alt"] = metadata.get("alt")


def load_photo(db_path: Path, photo_id: int) -> dict[str, Any] | None:
    photos = load_photos(db_path, limit=1, include_missing=True, photo_id=photo_id)
    return photos[0] if photos else None


def load_library_assets(
    db_path: Path,
    *,
    file_status: str = "",
    analysis_status: str = "",
    captured_from: str = "",
    captured_to: str = "",
    has_gps: bool | None = None,
    file_type: str = "",
    photo_type: str = "",
    directory: str = "",
    filename: str = "",
    sort: str = "created_at",
    order: str = "desc",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    """Return the administrator asset inventory without exposing absolute paths."""

    allowed_sorts = {
        "captured_at": "p.captured_at",
        "created_at": "p.created_at",
        "filename": "p.filename COLLATE NOCASE",
        "size": "p.size_bytes",
    }
    sort_expression = allowed_sorts.get(sort, allowed_sorts["created_at"])
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    safe_limit = max(1, min(int(limit or 200), 500))
    safe_offset = max(0, int(offset or 0))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "photos"):
            return {
                "items": [],
                "filtered_total": 0,
                "summary": {"total": 0, "analyzable": 0, "file_status": {}, "analysis_status": {}},
            }
        summary = {
            "total": int(conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]),
            "analyzable": int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM photos
                    WHERE file_status='present' AND exists_on_disk=1
                      AND visibility_status='active'
                    """
                ).fetchone()[0]
            ),
            "file_status": {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT file_status, COUNT(*) FROM photos GROUP BY file_status"
                )
            },
            "analysis_status": {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    "SELECT analysis_status, COUNT(*) FROM photos GROUP BY analysis_status"
                )
            },
        }

        clauses: list[str] = []
        params: list[Any] = []
        if file_status:
            clauses.append("p.file_status = ?")
            params.append(file_status)
        if analysis_status:
            clauses.append("p.analysis_status = ?")
            params.append(analysis_status)
        if captured_from:
            clauses.append("p.captured_at >= ?")
            params.append(captured_from)
        if captured_to:
            clauses.append("p.captured_at < date(?, '+1 day')")
            params.append(captured_to)
        if has_gps is True:
            clauses.append("p.gps_lat IS NOT NULL AND p.gps_lon IS NOT NULL")
        elif has_gps is False:
            clauses.append("(p.gps_lat IS NULL OR p.gps_lon IS NULL)")
        if file_type:
            normalized_type = file_type.strip().lower().lstrip(".")
            clauses.append(
                "(LOWER(LTRIM(p.file_extension, '.')) = ? OR LOWER(p.media_type) = ?)"
            )
            params.extend([normalized_type, normalized_type])
        if photo_type:
            clauses.append("COALESCE(av.photo_type, '') = ?")
            params.append(photo_type)
        if directory:
            clauses.append("p.relative_directory LIKE ?")
            params.append(f"%{directory}%")
        if filename:
            clauses.append("p.filename LIKE ?")
            params.append(f"%{filename}%")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        base = f"""
            FROM photos p
            LEFT JOIN analysis_versions av ON av.id = p.current_analysis_version_id
            {where}
        """
        filtered_total = int(
            conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
        )
        rows = conn.execute(
            f"""
            SELECT p.id, p.filename, p.relative_directory, p.file_extension,
                   p.media_type, p.size_bytes, p.width, p.height, p.file_status,
                   p.analysis_status, p.captured_at, p.gps_lat, p.gps_lon,
                   p.created_at, COALESCE(av.photo_type, '') AS photo_type
            {base}
            ORDER BY {sort_expression} {direction}, p.id {direction}
            LIMIT ? OFFSET ?
            """,
            params + [safe_limit, safe_offset],
        ).fetchall()
    finally:
        conn.close()

    return {
        "items": [
            {
                "photo_id": int(row["id"]),
                "filename": row["filename"] or "",
                "directory": row["relative_directory"] or "",
                "file_extension": row["file_extension"] or "",
                "media_type": row["media_type"] or "",
                "size_bytes": int(row["size_bytes"] or 0),
                "width": row["width"],
                "height": row["height"],
                "file_status": row["file_status"],
                "analysis_status": row["analysis_status"],
                "captured_at": row["captured_at"] or "",
                "has_gps": row["gps_lat"] is not None and row["gps_lon"] is not None,
                "type": row["photo_type"] or "",
                "created_at": row["created_at"],
                "preview_url": f"/media/previews/{int(row['id'])}.jpg",
            }
            for row in rows
        ],
        "filtered_total": filtered_total,
        "summary": summary,
    }


def load_library_source(db_path: Path, photo_id: int) -> Path | None:
    """Resolve an asset path for internal preview serving only."""

    conn = sqlite3.connect(db_path)
    try:
        if not _table_exists(conn, "photos"):
            return None
        row = conn.execute(
            "SELECT path FROM photos WHERE id=? AND exists_on_disk=1", (photo_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    source = Path(str(row[0])).expanduser()
    return source if source.is_file() else None
