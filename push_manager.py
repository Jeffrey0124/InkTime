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
    SIX_COLOR_PALETTE,
    _fit_single_line,
    _draw_location_pin,
    _load_text_font,
    _text_size,
    enhance_for_eink,
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


def _resolve_path(path: str | Path, *, base: Path = ROOT_DIR) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = base / resolved
    return resolved.resolve()


def _config_value(name: str, default: Any) -> Any:
    return getattr(cfg, name, default)


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
    return str(item.get("side_caption") or item.get("caption") or "未命名照片").strip()


def _draw_binary_text(
    canvas: Image.Image,
    xy: tuple[int, int],
    text: str,
    *,
    font,
    fill: tuple[int, int, int] = (0, 0, 0),
) -> None:
    mask = Image.new("L", canvas.size, 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.text(xy, text, fill=255, font=font)
    mask = mask.point(lambda value: 255 if value >= 96 else 0)
    canvas.paste(fill, mask=mask)


def _draw_caption_bar(
    canvas: Image.Image,
    *,
    item: dict[str, Any],
    settings: PushSettings,
) -> None:
    draw = ImageDraw.Draw(canvas)
    width = settings.width
    bar_top = settings.final_height - settings.caption_height
    margin = 28
    draw.rectangle((0, bar_top, width, settings.final_height), fill=(255, 255, 255))

    location = str(item.get("exif_city") or "").strip()
    icon_size = 16
    icon_gap = 6
    location_text = location
    location_font = _load_text_font(settings.font_path, 17)
    location_text_width = _text_size(draw, location_text, location_font)[0] if location_text else 0
    location_width = icon_size + icon_gap + location_text_width if location_text else 0
    caption_max_width = max(80, width - margin * 2 - location_width - (18 if location else 0))
    caption, caption_font = _fit_single_line(
        draw,
        _short_caption(item),
        font_path=settings.font_path,
        preferred_size=17,
        min_size=17,
        max_width=caption_max_width,
    )

    caption_h = _text_size(draw, caption, caption_font)[1]
    caption_y = bar_top + (settings.caption_height - caption_h) // 2 - 1
    _draw_binary_text(canvas, (margin, caption_y), caption, font=caption_font)

    if location_text:
        _, loc_h = _text_size(draw, location_text, location_font)
        icon_x = width - margin - location_width
        loc_x = icon_x + icon_size + icon_gap
        loc_y = bar_top + (settings.caption_height - loc_h) // 2 - 1
        icon_y = bar_top + (settings.caption_height - icon_size) // 2 - 1
        _draw_location_pin(draw, icon_x, icon_y, size=icon_size, fill=(0, 0, 0))
        _draw_binary_text(canvas, (loc_x, loc_y), location_text, font=location_font)


def build_push_image(item: dict[str, Any], settings: PushSettings) -> Image.Image:
    source = Path(str(item.get("source_path") or "")).expanduser()
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"源文件不存在: {source}")

    with Image.open(source) as img:
        fitted = fit_to_photopainter_canvas(
            img,
            width=settings.width,
            height=settings.image_height,
            mode=settings.mode,
            crop_focus=item.get("crop_focus"),
        )

    enhanced = enhance_for_eink(
        fitted,
        brightness=settings.brightness,
        contrast=settings.contrast,
        saturation=settings.saturation,
    )
    rendered_area = quantize_six_color(enhanced, settings.dither).convert("RGB")
    canvas = Image.new("RGB", (settings.width, settings.final_height), (255, 255, 255))
    canvas.paste(rendered_area, (0, 0))
    _draw_caption_bar(canvas, item=item, settings=settings)
    return canvas


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

    rendered = build_push_image(item, settings)
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
        "source_path": str(item.get("source_path") or ""),
        "render_width": settings.width,
        "render_height": settings.final_height,
        "image_height": settings.image_height,
        "caption_height": settings.caption_height,
        "side_caption": _short_caption(item),
        "exif_date": str(item.get("exif_date") or ""),
        "exif_city": str(item.get("exif_city") or ""),
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
                str(item.get("source_path") or ""),
                str(bmp_path),
                manifest["published_at"],
                trigger_type,
                slot or "",
                str(item.get("exif_date") or ""),
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
