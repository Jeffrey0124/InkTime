#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""把已分析照片渲染为本地 PhotoPainter Spectra 6 预览图。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from photopainter_renderer import DITHER_ATKINSON, render_photopainter_image


ROOT_DIR = Path(__file__).resolve().parent


def _resolve_path(path: str | Path, *, base: Path = ROOT_DIR) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = base / resolved
    return resolved.resolve()


def _extract_exif_date(exif_json: str | None) -> str:
    if not exif_json:
        return ""
    try:
        data = json.loads(exif_json)
    except json.JSONDecodeError:
        return ""
    raw = str(data.get("datetime") or "")
    if not raw:
        return ""
    date_part = raw.split()[0].replace(":", "-")
    parts = date_part.split("-")
    if len(parts) >= 3:
        return f"{parts[0]}-{parts[1]}-{parts[2]}"
    return ""


def _parse_crop_focus(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    required = ("x", "y", "w", "h")
    if not all(key in value for key in required):
        return None
    return value


def _safe_display_caption(side_caption: str, caption: str, ptype: str) -> str:
    text = str(side_caption or "").strip()
    if 4 <= len(text) <= 30 and "照片" not in text and "画面" not in text:
        return text
    combined = f"{ptype} {caption}"
    if any(word in combined for word in ("孩子", "儿童", "小女孩", "小朋友")):
        return "小手忙着搭一座新城"
    if any(word in combined for word in ("猫", "宠物", "狗")):
        return "它把日常占成了主角"
    if any(word in combined for word in ("旅行", "风景", "山", "海", "湖")):
        return "风景替脚步留了证词"
    if any(word in combined for word in ("美食", "餐", "饭", "菜")):
        return "胃先替记忆点了头"
    if any(word in combined for word in ("文档", "票", "收据", "截图")):
        return "一张纸也记得来路"
    return "日常在这里轻轻落座"


def _native_location_from_source(source: Path) -> str:
    try:
        from analyze_photos import get_city_resolver, read_exif

        info = read_exif(source)
        lat = info.get("gps_lat")
        lon = info.get("gps_lon")
        if lat is None or lon is None:
            return ""
        city = get_city_resolver()(float(lat), float(lon))
        if city:
            return city
        return f"{float(lat):.4f}, {float(lon):.4f}"
    except Exception:
        return ""


def _load_rows(db_path: Path, limit: int | None) -> list[dict[str, Any]]:
    if not db_path.exists():
        raise FileNotFoundError(f"找不到数据库文件: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(photo_scores)").fetchall()
        }
        analysis_channel_expr = (
            "analysis_channel" if "analysis_channel" in columns else "'' AS analysis_channel"
        )
        analysis_model_expr = (
            "analysis_model" if "analysis_model" in columns else "'' AS analysis_model"
        )
        crop_focus_expr = (
            "crop_focus_json" if "crop_focus_json" in columns else "'' AS crop_focus_json"
        )
        location_hint_expr = (
            "location_hint" if "location_hint" in columns else "'' AS location_hint"
        )
        rows = conn.execute(
            f"""
            SELECT path,
                   caption,
                   type,
                   memory_score,
                   beauty_score,
                   reason,
                   exif_json,
                   side_caption,
                   exif_city,
                   {location_hint_expr},
                   {analysis_channel_expr},
                   {analysis_model_expr},
                   {crop_focus_expr}
            FROM photo_scores
            ORDER BY COALESCE(memory_score, -1) DESC,
                     COALESCE(beauty_score, -1) DESC,
                     path ASC
            """
        ).fetchall()
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        source = Path(str(row["path"]))
        if not source.exists():
            continue
        caption = row["caption"] or ""
        ptype = row["type"] or ""
        side_caption = _safe_display_caption(row["side_caption"] or "", caption, ptype)
        location = row["exif_city"] or _native_location_from_source(source)
        items.append(
            {
                "source_path": str(source),
                "caption": caption,
                "type": ptype,
                "memory_score": row["memory_score"],
                "beauty_score": row["beauty_score"],
                "reason": row["reason"] or "",
                "exif_date": _extract_exif_date(row["exif_json"]),
                "side_caption": side_caption,
                "exif_city": location,
                "location_hint": location,
                "analysis_channel": row["analysis_channel"] or "",
                "analysis_model": row["analysis_model"] or "",
                "crop_focus": _parse_crop_focus(row["crop_focus_json"]),
            }
        )
        if limit is not None and len(items) >= limit:
            break
    return items


def render_from_database(
    *,
    db_path: str | Path,
    output_dir: str | Path,
    limit: int | None = None,
    width: int = 800,
    height: int = 432,
    final_height: int = 480,
    caption_height: int = 48,
    mode: str = "scale",
    dither: str = DITHER_ATKINSON,
    brightness: float = 1.1,
    contrast: float = 1.2,
    saturation: float = 1.2,
    save_bmp: bool = True,
    font_path: str | Path | None = None,
) -> dict[str, Any]:
    db = _resolve_path(db_path)
    out_dir = _resolve_path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_rows(db, limit)
    renders: list[dict[str, Any]] = []

    for idx, item in enumerate(rows):
        png_name = f"render_{idx:03d}.png"
        png_path = out_dir / png_name
        render_photopainter_image(
            item["source_path"],
            png_path,
            width=width,
            height=height,
            mode=mode,
            dither=dither,
            brightness=brightness,
            contrast=contrast,
            saturation=saturation,
            save_bmp=save_bmp,
            crop_focus=item.get("crop_focus"),
        )
        render_item = {
            **item,
            "render_png": png_name,
            "render_bmp": f"render_{idx:03d}.bmp" if save_bmp else "",
        }
        renders.append(render_item)

    manifest = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "width": width,
        "height": height,
        "final_width": width,
        "final_height": final_height,
        "caption_height": caption_height,
        "mode": mode,
        "dither": dither,
        "brightness": brightness,
        "contrast": contrast,
        "saturation": saturation,
        "renders": renders,
    }

    if renders:
        shutil.copyfile(out_dir / renders[0]["render_png"], out_dir / "latest.png")
        if save_bmp and renders[0]["render_bmp"]:
            shutil.copyfile(out_dir / renders[0]["render_bmp"], out_dir / "latest.bmp")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _load_config_defaults() -> dict[str, Any]:
    try:
        import config as cfg
    except ModuleNotFoundError:
        cfg = object()

    return {
        "db_path": getattr(cfg, "DB_PATH", "./photos.db"),
        "output_dir": getattr(cfg, "RENDER_OUTPUT_DIR", "./output/photopainter"),
        "limit": getattr(cfg, "DAILY_PHOTO_QUANTITY", 5),
        "width": getattr(cfg, "RENDER_WIDTH", 800),
        "height": getattr(cfg, "RENDER_HEIGHT", 432),
        "final_height": getattr(cfg, "FINAL_RENDER_HEIGHT", 480),
        "caption_height": getattr(cfg, "CAPTION_BAR_HEIGHT", 48),
        "mode": getattr(cfg, "RENDER_MODE", "scale"),
        "dither": getattr(cfg, "DITHER_MODE", DITHER_ATKINSON),
        "brightness": getattr(cfg, "BRIGHTNESS", 1.1),
        "contrast": getattr(cfg, "CONTRAST", 1.2),
        "saturation": getattr(cfg, "SATURATION", 1.2),
        "save_bmp": getattr(cfg, "SAVE_BMP_OUTPUT", True),
        "font_path": getattr(cfg, "FONT_PATH", ""),
    }


def main() -> None:
    defaults = _load_config_defaults()
    parser = argparse.ArgumentParser(description="渲染 PhotoPainter Spectra 6 六色预览图")
    parser.add_argument("--db-path", default=defaults["db_path"])
    parser.add_argument("--output-dir", default=defaults["output_dir"])
    parser.add_argument("--limit", type=int, default=defaults["limit"])
    parser.add_argument("--width", type=int, default=defaults["width"])
    parser.add_argument("--height", type=int, default=defaults["height"])
    parser.add_argument("--mode", choices=["cut", "scale"], default=defaults["mode"])
    parser.add_argument(
        "--dither",
        choices=["atkinson", "floyd-steinberg", "none"],
        default=defaults["dither"],
    )
    parser.add_argument("--brightness", type=float, default=defaults["brightness"])
    parser.add_argument("--contrast", type=float, default=defaults["contrast"])
    parser.add_argument("--saturation", type=float, default=defaults["saturation"])
    parser.add_argument("--no-bmp", action="store_true", default=not defaults["save_bmp"])
    args = parser.parse_args()

    manifest = render_from_database(
        db_path=args.db_path,
        output_dir=args.output_dir,
        limit=args.limit,
        width=args.width,
        height=args.height,
        final_height=defaults["final_height"],
        caption_height=defaults["caption_height"],
        mode=args.mode,
        dither=args.dither,
        brightness=args.brightness,
        contrast=args.contrast,
        saturation=args.saturation,
        save_bmp=not args.no_bmp,
        font_path=defaults["font_path"],
    )
    print(f"[OK] 已渲染 {len(manifest['renders'])} 张 PhotoPainter 预览图")
    print(f"[OK] 输出目录: {_resolve_path(args.output_dir)}")


if __name__ == "__main__":
    main()
