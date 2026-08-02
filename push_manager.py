#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""发布 PhotoPainter 设备可下载的 BMP 成品图。"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, ImageDraw

from photopainter_renderer import (
    DITHER_ATKINSON,
    DITHER_NONE,
    SIX_COLOR_PALETTE,
    _fit_single_line,
    _draw_location_pin,
    _load_text_font,
    _text_size,
    enhance_for_eink,
    fit_manual_transform_to_canvas,
    fit_to_photopainter_canvas,
    quantize_six_color,
)
from render_photopainter import _load_rows


ROOT_DIR = Path(__file__).resolve().parent

try:
    import config as cfg
except ModuleNotFoundError:
    cfg = object()


@dataclass(frozen=True)
class PushSettings:
    db_path: Path
    render_output_dir: Path
    push_output_dir: Path
    width: int = 800
    image_height: int = 432
    final_height: int = 480
    caption_height: int = 48
    mode: str = "scale"
    dither: str = DITHER_ATKINSON
    brightness: float = 1.1
    contrast: float = 1.2
    saturation: float = 1.2
    font_path: str | Path | None = None
    timezone: str = "Asia/Shanghai"
    exclude_days: int = 90


@dataclass(frozen=True)
class PushLayout:
    width: int
    image_height: int
    final_height: int
    caption_height: int
    portrait: bool = False


def _layout_for_orientation(settings: PushSettings, orientation: str) -> PushLayout:
    if str(orientation).lower() == "portrait":
        caption_height = max(1, settings.caption_height * 2)
        final_height = settings.width
        return PushLayout(
            width=settings.final_height,
            image_height=final_height - caption_height,
            final_height=final_height,
            caption_height=caption_height,
            portrait=True,
        )
    return PushLayout(
        width=settings.width,
        image_height=settings.image_height,
        final_height=settings.final_height,
        caption_height=settings.caption_height,
    )


def _resolve_path(path: str | Path, *, base: Path = ROOT_DIR) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = base / resolved
    return resolved.resolve()


def _config_value(name: str, default: Any) -> Any:
    return getattr(cfg, name, default)


def normalize_render_overrides(value: Any) -> dict[str, Any]:
    saved = _json_object(value)
    normalized: dict[str, Any] = {
        "show_caption": True,
        "show_date": True,
        "show_location": True,
        "frame_orientation": "landscape",
    }
    normalized.update(saved)
    normalized["display_defaults_version"] = 2
    if normalized.get("frame_orientation") not in {"landscape", "portrait"}:
        normalized["frame_orientation"] = "landscape"
    return normalized


def timezone_from_name(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {"Asia/Shanghai", "PRC", "CST", "UTC+8"}:
            return dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")
        if name.upper() == "UTC":
            return dt.timezone.utc
        raise


def settings_from_config(
    *,
    db_path: str | Path | None = None,
    render_output_dir: str | Path | None = None,
    push_output_dir: str | Path | None = None,
) -> PushSettings:
    return PushSettings(
        db_path=_resolve_path(db_path or _config_value("DB_PATH", "./photos.db")),
        render_output_dir=_resolve_path(
            render_output_dir or _config_value("RENDER_OUTPUT_DIR", "./output/photopainter")
        ),
        push_output_dir=_resolve_path(
            push_output_dir or _config_value("PUSH_OUTPUT_DIR", "./output/push")
        ),
        width=int(_config_value("RENDER_WIDTH", 800)),
        image_height=int(_config_value("RENDER_HEIGHT", 432)),
        final_height=int(_config_value("FINAL_RENDER_HEIGHT", 480)),
        caption_height=int(_config_value("CAPTION_BAR_HEIGHT", 48)),
        mode=str(_config_value("RENDER_MODE", "scale")),
        dither=str(_config_value("DITHER_MODE", DITHER_ATKINSON)),
        brightness=float(_config_value("BRIGHTNESS", 1.1)),
        contrast=float(_config_value("CONTRAST", 1.2)),
        saturation=float(_config_value("SATURATION", 1.2)),
        font_path=str(_config_value("FONT_PATH", "")) or None,
        timezone=str(_config_value("PUSH_TIMEZONE", "Asia/Shanghai")),
        exclude_days=int(_config_value("PUSH_EXCLUDE_DAYS", 90)),
    )


def ensure_push_schema(db_path: str | Path) -> None:
    db = _resolve_path(db_path)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS push_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_path TEXT NOT NULL,
              render_path TEXT NOT NULL,
              pushed_at TEXT NOT NULL,
              trigger_type TEXT NOT NULL,
              slot TEXT,
              exif_date TEXT,
              note TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _load_manifest(render_output_dir: Path) -> dict[str, Any]:
    manifest_path = render_output_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"找不到渲染 manifest: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _render_item_from_manifest(render_id: int, settings: PushSettings) -> dict[str, Any]:
    manifest = _load_manifest(settings.render_output_dir)
    renders = manifest.get("renders", [])
    if render_id < 0 or render_id >= len(renders):
        raise IndexError(f"render_id 超出范围: {render_id}")
    item = dict(renders[render_id])
    item["render_id"] = render_id
    return item


def _short_caption(item: dict[str, Any]) -> str:
    if item.get("_hide_caption"):
        return ""
    return str(item.get("side_caption") or item.get("caption") or "未命名照片").strip()


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _with_saved_overrides(item: dict[str, Any], db_path: Path) -> dict[str, Any]:
    """让手动推送和定时推送复用 WebUI 保存的单张参数。"""
    source_path = str(item.get("source_path") or "")
    if not source_path or not db_path.exists():
        return dict(item)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT o.custom_side_caption, o.manual_crop_json, o.render_overrides_json
            FROM photos p
            JOIN photo_overrides o ON o.photo_id = p.id
            WHERE p.path = ?
            """,
            (source_path,),
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()
    result = dict(item)
    if row is not None:
        if row["custom_side_caption"]:
            result["side_caption"] = row["custom_side_caption"]
        result["manual_crop_json"] = row["manual_crop_json"] or ""
        result["render_overrides_json"] = row["render_overrides_json"] or ""
    return result


def _draw_caption_bar(
    canvas: Image.Image,
    *,
    item: dict[str, Any],
    settings: PushSettings,
    layout: PushLayout,
) -> None:
    draw = ImageDraw.Draw(canvas)
    width = layout.width
    bar_top = layout.final_height - layout.caption_height
    margin = 24 if layout.portrait else 28
    draw.rectangle((0, bar_top, width, layout.final_height), fill=(255, 255, 255))

    location = str(item.get("exif_city") or "").strip()
    icon_size = 16
    icon_gap = 6
    location_text = location
    location_font = _load_text_font(settings.font_path, 17)
    location_text_width = _text_size(draw, location_text, location_font)[0] if location_text else 0
    location_width = icon_size + icon_gap + location_text_width if location_text else 0
    caption_max_width = max(
        80,
        width - margin * 2 if layout.portrait else width - margin * 2 - location_width - (18 if location else 0),
    )
    caption, caption_font = _fit_single_line(
        draw,
        _short_caption(item),
        font_path=settings.font_path,
        preferred_size=20 if layout.portrait else 17,
        min_size=15 if layout.portrait else 17,
        max_width=caption_max_width,
    )

    caption_h = _text_size(draw, caption, caption_font)[1]
    caption_y = (
        bar_top + 24
        if layout.portrait
        else bar_top + (layout.caption_height - caption_h) // 2 - 1
    )
    draw.text((margin, caption_y), caption, fill=(0, 0, 0), font=caption_font)

    if location_text:
        _, loc_h = _text_size(draw, location_text, location_font)
        icon_x = margin if layout.portrait else width - margin - location_width
        loc_x = icon_x + icon_size + icon_gap
        loc_y = (
            bar_top + 72
            if layout.portrait
            else bar_top + (layout.caption_height - loc_h) // 2 - 1
        )
        icon_y = (
            bar_top + 71
            if layout.portrait
            else bar_top + (layout.caption_height - icon_size) // 2 - 1
        )
        _draw_location_pin(draw, icon_x, icon_y, size=icon_size, fill=(0, 0, 0))
        draw.text((loc_x, loc_y), location_text, fill=(0, 0, 0), font=location_font)


def build_push_image(item: dict[str, Any], settings: PushSettings) -> Image.Image:
    source = Path(str(item.get("source_path") or "")).expanduser()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"源文件不存在: {source}")

    manual_crop = _json_object(item.get("manual_crop_json"))
    render_overrides = normalize_render_overrides(item.get("render_overrides_json"))
    layout = _layout_for_orientation(
        settings, str(render_overrides.get("frame_orientation", "landscape"))
    )
    with Image.open(source) as img:
        if manual_crop:
            fitted = fit_manual_transform_to_canvas(
                img,
                width=layout.width,
                height=layout.image_height,
                scale=float(manual_crop.get("scale", 1.0)),
                offset_x=float(manual_crop.get("offset_x", manual_crop.get("x", 0.0))),
                offset_y=float(manual_crop.get("offset_y", manual_crop.get("y", 0.0))),
                rotation=int(manual_crop.get("rotation", 0)),
                fit_mode=str(manual_crop.get("fit_mode", "fill")),
            )
        else:
            fitted = fit_to_photopainter_canvas(
                img,
                width=layout.width,
                height=layout.image_height,
                mode=settings.mode,
                crop_focus=item.get("crop_focus"),
            )

    enhanced = enhance_for_eink(
        fitted,
        brightness=float(render_overrides.get("brightness", settings.brightness)),
        contrast=float(render_overrides.get("contrast", settings.contrast)),
        saturation=float(render_overrides.get("saturation", settings.saturation)),
    )
    canvas = Image.new("RGB", (layout.width, layout.final_height), (255, 255, 255))
    canvas.paste(enhanced, (0, 0))
    caption_item = dict(item)
    if not bool(render_overrides.get("show_caption", True)):
        caption_item["_hide_caption"] = True
        caption_item["side_caption"] = ""
        caption_item["caption"] = ""
    location = (
        str(caption_item.get("exif_city") or "").strip()
        if bool(render_overrides.get("show_location", True))
        else ""
    )
    date = (
        str(item.get("exif_date") or "").strip()
        if bool(render_overrides.get("show_date", True))
        else ""
    )
    caption_item["exif_city"] = " · ".join(value for value in (location, date) if value)
    _draw_caption_bar(canvas, item=caption_item, settings=settings, layout=layout)
    dither = str(render_overrides.get("dither_type", settings.dither))
    if not bool(render_overrides.get("dither_enabled", True)):
        dither = DITHER_NONE
    return quantize_six_color(
        canvas,
        dither,
        strength=float(render_overrides.get("dither_strength", 1.0)),
    ).convert("RGB")


def _now(settings: PushSettings, now: dt.datetime | None = None) -> dt.datetime:
    timezone = timezone_from_name(settings.timezone)
    if now is not None:
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone)
        return now.astimezone(timezone)
    return dt.datetime.now(timezone)


def write_latest_files(
    item: dict[str, Any],
    *,
    settings: PushSettings,
    trigger_type: str,
    slot: str | None = None,
    note: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    ensure_push_schema(settings.db_path)
    settings.push_output_dir.mkdir(parents=True, exist_ok=True)
    published_at = _now(settings, now)

    effective_item = _with_saved_overrides(item, settings.db_path)
    rendered = build_push_image(effective_item, settings)
    manual_crop = _json_object(effective_item.get("manual_crop_json"))
    render_overrides = normalize_render_overrides(effective_item.get("render_overrides_json"))
    layout = _layout_for_orientation(
        settings, str(render_overrides.get("frame_orientation", "landscape"))
    )
    bmp_path = settings.push_output_dir / "latest.bmp"
    png_path = settings.push_output_dir / "latest.png"
    manifest_path = settings.push_output_dir / "manifest.json"
    rendered.save(bmp_path)
    rendered.save(png_path)

    manifest = {
        "image_url": "/push/latest.bmp",
        "preview_url": "/push/latest.png",
        "format": "bmp24",
        "published_at": published_at.isoformat(timespec="seconds"),
        "trigger_type": trigger_type,
        "slot": slot or "",
        "source_path": str(effective_item.get("source_path") or ""),
        "render_width": layout.width,
        "render_height": layout.final_height,
        "image_height": layout.image_height,
        "caption_height": layout.caption_height,
        "side_caption": _short_caption(effective_item),
        "exif_date": str(effective_item.get("exif_date") or ""),
        "exif_city": str(effective_item.get("exif_city") or ""),
        "manual_crop": manual_crop,
        "render_overrides": render_overrides,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            """
            INSERT INTO push_history
            (source_path, render_path, pushed_at, trigger_type, slot, exif_date, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(effective_item.get("source_path") or ""),
                str(bmp_path),
                manifest["published_at"],
                trigger_type,
                slot or "",
                str(effective_item.get("exif_date") or ""),
                note or "",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return manifest


def publish_render(
    render_id: int,
    *,
    settings: PushSettings | None = None,
    trigger_type: str = "manual",
    slot: str | None = None,
    note: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    active_settings = settings or settings_from_config()
    item = _render_item_from_manifest(render_id, active_settings)
    return write_latest_files(
        item,
        settings=active_settings,
        trigger_type=trigger_type,
        slot=slot,
        note=note,
        now=now,
    )


def _parse_exif_date(value: str | None) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y:%m:%d"):
        try:
            return dt.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _day_distance(exif_date: str | None, today: dt.date) -> int:
    parsed = _parse_exif_date(exif_date)
    if parsed is None:
        return 999
    try:
        candidate = parsed.replace(year=today.year)
    except ValueError:
        candidate = dt.date(today.year, 2, 28)
    delta = abs((candidate - today).days)
    return min(delta, 366 - delta)


def _recent_sources(settings: PushSettings, now: dt.datetime) -> set[str]:
    ensure_push_schema(settings.db_path)
    cutoff = now - dt.timedelta(days=settings.exclude_days)
    conn = sqlite3.connect(settings.db_path)
    try:
        rows = conn.execute(
            """
            SELECT source_path FROM push_history
            WHERE pushed_at >= ?
            """,
            (cutoff.isoformat(timespec="seconds"),),
        ).fetchall()
    finally:
        conn.close()
    return {str(row[0]) for row in rows}


def _daily_candidates(
    now: dt.datetime | None = None,
    *,
    settings: PushSettings | None = None,
) -> list[dict[str, Any]]:
    active_settings = settings or settings_from_config()
    current = _now(active_settings, now)
    rows = _load_rows(active_settings.db_path, None)
    candidates = [item for item in rows if Path(str(item.get("source_path") or "")).exists()]
    if not candidates:
        raise FileNotFoundError("没有可推送的候选照片")

    recent = _recent_sources(active_settings, current)
    fresh = [item for item in candidates if str(item.get("source_path") or "") not in recent]
    pool = fresh or candidates

    today = current.date()

    def sort_key(item: dict[str, Any]) -> tuple[int, float, float]:
        return (
            _day_distance(item.get("exif_date"), today),
            -float(item.get("memory_score") or 0),
            -float(item.get("beauty_score") or 0),
        )

    return sorted(pool, key=sort_key)


def select_daily_photo(
    now: dt.datetime | None = None,
    *,
    settings: PushSettings | None = None,
) -> dict[str, Any]:
    candidates = _daily_candidates(now, settings=settings)
    return candidates[0]


def publish_scheduled(
    *,
    slot: str | None = None,
    settings: PushSettings | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    active_settings = settings or settings_from_config()
    last_error: Exception | None = None
    for item in _daily_candidates(now, settings=active_settings):
        try:
            return write_latest_files(
                item,
                settings=active_settings,
                trigger_type="scheduled",
                slot=slot,
                now=now,
            )
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"没有可成功生成 BMP 的候选照片：{last_error}")


def verify_six_color(image_path: str | Path) -> bool:
    with Image.open(image_path) as img:
        colors = set(img.convert("RGB").getdata())
    return colors <= set(SIX_COLOR_PALETTE)
