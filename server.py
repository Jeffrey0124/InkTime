#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""纯软件版 PhotoPainter 本地预览服务。"""

from __future__ import annotations

import html
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, redirect, request, send_file

from photo_identity import ensure_photo_identity_schema
from push_manager import PushSettings, ensure_push_schema, publish_render
from render_photopainter import render_from_database
from web_queries import load_photo, load_photos, load_status


ROOT_DIR = Path(__file__).resolve().parent

try:
    import config as cfg
except ModuleNotFoundError:
    cfg = object()


def _resolve_path(path: str | Path, *, base: Path = ROOT_DIR) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = base / resolved
    return resolved.resolve()


def _config_value(name: str, default: Any) -> Any:
    return getattr(cfg, name, default)


def _load_manifest(render_output_dir: Path) -> dict[str, Any]:
    manifest_path = render_output_dir / "manifest.json"
    if not manifest_path.exists():
        return {
            "width": _config_value("RENDER_WIDTH", 800),
            "height": _config_value("RENDER_HEIGHT", 432),
            "final_width": _config_value("RENDER_WIDTH", 800),
            "final_height": _config_value("FINAL_RENDER_HEIGHT", 480),
            "caption_height": _config_value("CAPTION_BAR_HEIGHT", 48),
            "mode": _config_value("RENDER_MODE", "scale"),
            "dither": _config_value("DITHER_MODE", "atkinson"),
            "renders": [],
        }
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _read_review_rows(db_path: Path, limit: int = 200) -> list[sqlite3.Row]:
    if not db_path.exists():
        return []
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
        location_hint_expr = (
            "location_hint" if "location_hint" in columns else "'' AS location_hint"
        )
        return conn.execute(
            f"""
            SELECT path,
                   caption,
                   type,
                   memory_score,
                   beauty_score,
                   reason,
                   side_caption,
                   exif_city,
                   {location_hint_expr},
                   {analysis_channel_expr},
                   {analysis_model_expr}
            FROM photo_scores
            ORDER BY COALESCE(memory_score, -1) DESC,
                     COALESCE(beauty_score, -1) DESC,
                     path ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def _esc(value: Any) -> str:
    return html.escape(str(value or ""))


def _fmt_score(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return str(value)


def _score_width(value: Any) -> int:
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        return 0


def _short_path(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    path = Path(text)
    parts = path.parts
    if len(parts) >= 3:
        return str(Path(*parts[-3:]))
    return text


def _type_pills(value: Any) -> str:
    raw = str(value or "未分类")
    labels = [part.strip() for part in re.split(r"[/,，、\s]+", raw) if part.strip()]
    if not labels:
        labels = ["未分类"]
    return "".join(f'<span class="pill">{_esc(label)}</span>' for label in labels[:4])


def _channel_badge(value: Any) -> str:
    channel = str(value or "").strip()
    if not channel:
        return ""
    return f'<span class="chip">分析 {_esc(channel)}</span>'


def _display_side_caption(item: dict[str, Any]) -> str:
    side = str(item.get("side_caption") or "").strip()
    if 4 <= len(side) <= 30 and "照片" not in side and "画面" not in side:
        return side
    caption = str(item.get("caption") or "")
    ptype = str(item.get("type") or "")
    combined = f"{ptype} {caption}"
    if any(word in combined for word in ("孩子", "儿童", "小女孩", "小朋友")):
        return "小手忙着搭一座新城"
    if any(word in combined for word in ("猫", "宠物", "狗")):
        return "它把日常占成了主角"
    if any(word in combined for word in ("旅行", "风景", "山", "海", "湖")):
        return "风景替脚步留了证词"
    if any(word in combined for word in ("美食", "餐", "饭", "菜")):
        return "胃先替记忆点了头"
    return "日常在这里轻轻落座"


def _display_location(item: dict[str, Any]) -> str:
    return str(item.get("exif_city") or "").strip()


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --page: #f7f8f4;
      --panel: rgba(255, 255, 252, 0.94);
      --panel-strong: rgba(255, 255, 252, 0.98);
      --line: rgba(22, 34, 27, 0.13);
      --line-soft: rgba(22, 34, 27, 0.08);
      --text: #121711;
      --muted: #667168;
      --subtle: #8b938c;
      --mint: #8ff0c2;
      --gold: #ffd36e;
      --coral: #ff9a72;
      --blue: #7ab8ff;
      --green: #0b7d4b;
      --shadow: 0 20px 70px rgba(31, 45, 36, 0.12);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font: 14px/1.65 "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      color: var(--text);
      background:
        linear-gradient(120deg, rgba(221, 244, 232, 0.8), transparent 36%),
        linear-gradient(270deg, rgba(242, 226, 198, 0.48), transparent 42%),
        var(--page);
    }}
    a {{ color: inherit; text-decoration: none; }}
    .topbar {{
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 16px clamp(18px, 4vw, 54px);
      border-bottom: 1px solid var(--line-soft);
      background: rgba(247, 248, 244, 0.86);
      backdrop-filter: blur(18px);
    }}
    .brand {{
      display: flex;
      flex-direction: column;
      gap: 1px;
      min-width: 0;
    }}
    .brand strong {{
      font-size: 17px;
      letter-spacing: 0;
      white-space: nowrap;
    }}
    .brand span {{
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    nav {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    nav a, .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 34px;
      padding: 7px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--text);
      font-weight: 650;
    }}
    nav a:hover, .button:hover, .render-card:hover {{
      border-color: rgba(143, 240, 194, 0.48);
    }}
    main {{
      width: min(1460px, calc(100vw - 40px));
      margin: 24px auto 56px;
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: end;
      margin-bottom: 18px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(26px, 4vw, 42px);
      line-height: 1.18;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 16px;
      letter-spacing: 0;
    }}
    .lead {{
      margin: 9px 0 0;
      max-width: 820px;
      color: var(--muted);
      font-size: 15px;
    }}
    .stats {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }}
    .chip, .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 5px 10px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }}
    .pill {{
      border-color: rgba(11, 125, 75, 0.2);
      background: rgba(11, 125, 75, 0.08);
      color: var(--green);
    }}
    .gallery-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 18px;
    }}
    .render-card {{
      display: grid;
      overflow: hidden;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }}
    .thumb {{
      display: block;
      width: 100%;
      aspect-ratio: 50 / 27;
      object-fit: contain;
      background: #f5f1e8;
      border-bottom: 1px solid var(--line-soft);
    }}
    .card-copy {{
      min-width: 0;
      padding: 13px 14px 15px;
    }}
    .caption {{
      margin: 0 0 7px;
      font-size: 15px;
      font-weight: 750;
      line-height: 1.45;
    }}
    .muted {{
      color: var(--muted);
    }}
    .small {{
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    .score-line {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 10px;
      color: var(--mint);
      font-weight: 800;
    }}
    .empty {{
      padding: 32px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--muted);
    }}
    .dashboard-grid {{
      display: grid;
      grid-template-columns: minmax(360px, 1.15fr) minmax(320px, 0.85fr);
      gap: 18px;
      align-items: stretch;
    }}
    .status-panel, .metric-card, .photo-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }}
    .status-panel {{
      padding: clamp(22px, 3vw, 34px);
    }}
    .status-kicker {{
      margin: 0 0 14px;
      color: var(--green);
      font-weight: 850;
    }}
    .status-title {{
      margin: 0;
      max-width: 780px;
      font-size: clamp(28px, 4vw, 52px);
      line-height: 1.12;
      letter-spacing: 0;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0;
    }}
    .metric-card {{
      padding: 16px;
      min-width: 0;
    }}
    .metric-card span {{
      display: block;
      color: var(--muted);
      font-size: 12px;
    }}
    .metric-card strong {{
      display: block;
      margin-top: 5px;
      font-size: clamp(24px, 3vw, 38px);
      line-height: 1;
    }}
    .dashboard-actions, .gallery-toolbar {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
    }}
    .latest-push {{
      display: grid;
      gap: 12px;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
    }}
    .latest-push img {{
      width: 100%;
      aspect-ratio: 5 / 3;
      object-fit: contain;
      border-radius: 8px;
      background: #f5f1e8;
      border: 1px solid var(--line-soft);
    }}
    .gallery-toolbar {{
      justify-content: space-between;
      margin: 0 0 18px;
      padding: 14px 0;
      border-top: 1px solid var(--line-soft);
      border-bottom: 1px solid var(--line-soft);
    }}
    select {{
      min-height: 38px;
      padding: 7px 34px 7px 12px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.82);
      color: var(--text);
      font: inherit;
      font-weight: 650;
    }}
    .photo-masonry {{
      columns: 3 280px;
      column-gap: 18px;
    }}
    .photo-card {{
      position: relative;
      display: inline-block;
      width: 100%;
      overflow: hidden;
      margin: 0 0 18px;
      break-inside: avoid;
    }}
    .photo-card img {{
      display: block;
      width: 100%;
      height: auto;
      background: #f5f1e8;
    }}
    .photo-card-copy {{
      padding: 14px 15px 16px;
    }}
    .score-badge {{
      position: absolute;
      right: 12px;
      bottom: 78px;
      display: grid;
      place-items: center;
      min-width: 58px;
      min-height: 58px;
      padding: 8px;
      border-radius: 999px;
      background: #111711;
      color: #fff;
      font-weight: 900;
      line-height: 1.05;
      text-align: center;
      box-shadow: 0 12px 30px rgba(17, 23, 17, 0.24);
    }}
    .score-badge small {{
      display: block;
      font-size: 10px;
      font-weight: 700;
      color: rgba(255,255,255,.75);
    }}
    .photo-card-actions {{
      position: absolute;
      top: 12px;
      right: 12px;
      display: flex;
      gap: 8px;
      opacity: 0;
      transform: translateY(-4px);
      transition: opacity .16s ease, transform .16s ease;
    }}
    .photo-card:hover .photo-card-actions,
    .photo-card:focus-within .photo-card-actions {{
      opacity: 1;
      transform: translateY(0);
    }}
    .primary-button {{
      border-color: rgba(11, 125, 75, 0.2);
      background: #22b573;
      color: #fff;
      box-shadow: 0 12px 28px rgba(34, 181, 115, 0.2);
    }}
    .detail-grid {{
      display: grid;
      grid-template-columns: minmax(320px, 800px) minmax(320px, 1fr);
      gap: 26px;
      align-items: start;
    }}
    .preview-panel {{
      display: grid;
      grid-template-rows: minmax(0, 1fr) 10%;
      aspect-ratio: 5 / 3;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
      box-shadow: var(--shadow);
    }}
    .preview-panel img {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #f5f1e8;
    }}
    .paper-caption {{
      padding: 14px 18px;
      background: #f5f1e8;
      color: #1d1d1b;
      font-size: 17px;
      line-height: 1.45;
      font-family: "KaiTi", "STKaiti", "Microsoft YaHei", serif;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      min-height: 0;
      overflow: hidden;
    }}
    .paper-caption-text {{
      min-width: 0;
    }}
    .paper-location {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: #8a877f;
      flex: 0 0 auto;
      white-space: nowrap;
    }}
    .paper-location img {{
      display: block;
      width: 20px;
      height: 20px;
      opacity: 0.75;
    }}
    .info-panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: var(--shadow);
      padding: clamp(18px, 3vw, 28px);
      overflow: auto;
    }}
    .quote-title {{
      margin: 0 0 14px;
      font-size: clamp(22px, 3vw, 34px);
      line-height: 1.25;
      letter-spacing: 0;
    }}
    .pills {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 20px;
    }}
    .description {{
      margin: 0 0 18px;
      color: var(--text);
      font-size: 15px;
    }}
    .score-row {{
      display: grid;
      grid-template-columns: 58px minmax(120px, 1fr) 44px;
      gap: 10px;
      align-items: center;
      margin: 11px 0;
    }}
    .bar {{
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.12);
    }}
    .bar span {{
      display: block;
      width: var(--value);
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--blue), var(--mint));
    }}
    .score-row:nth-of-type(2) .bar span {{
      background: linear-gradient(90deg, var(--gold), var(--coral));
    }}
    .reason {{
      margin-top: 20px;
      padding-top: 18px;
      border-top: 1px solid var(--line-soft);
    }}
    .reason strong {{
      display: block;
      margin-bottom: 6px;
    }}
    details {{
      margin-top: 20px;
      border: 1px solid var(--line-soft);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.03);
    }}
    summary {{
      cursor: pointer;
      padding: 12px 14px;
      color: var(--text);
      font-weight: 700;
    }}
    .detail-list {{
      display: grid;
      gap: 8px;
      padding: 0 14px 14px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }}
    .actions {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 18px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }}
    th, td {{
      padding: 13px 14px;
      border-bottom: 1px solid var(--line-soft);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    @media (max-width: 900px) {{
      .topbar, .hero {{
        align-items: flex-start;
        grid-template-columns: 1fr;
      }}
      .topbar {{
        flex-direction: column;
      }}
      nav, .stats {{
        justify-content: flex-start;
      }}
      main {{
        width: min(100% - 24px, 1460px);
        margin-top: 18px;
      }}
      .detail-grid {{
        grid-template-columns: 1fr;
      }}
      .gallery-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/">
      <strong>InkTime PhotoPainter</strong>
      <span>照片瀑布流 | 批量预览 | 设备推送</span>
    </a>
    <nav><a href="/">中控台</a><a href="/gallery">画廊</a><a href="/renders">旧渲染</a><a href="/review">分析结果</a></nav>
  </header>
  <main>{body}</main>
  <script>
    (() => {{
      const syncDetailPanels = () => {{
        document.querySelectorAll(".detail-grid").forEach((grid) => {{
          const preview = grid.querySelector(".preview-panel");
          const info = grid.querySelector(".info-panel");
          if (!preview || !info) return;
          if (window.matchMedia("(max-width: 900px)").matches) {{
            info.style.height = "";
            return;
          }}
          info.style.height = `${{Math.round(preview.getBoundingClientRect().height)}}px`;
        }});
      }};
      window.pushRender = async (renderId) => {{
        const headers = {{}};
        const savedToken = sessionStorage.getItem("inktimePushToken");
        if (savedToken) headers["X-Push-Token"] = savedToken;
        let response = await fetch(`/api/push/manual/${{renderId}}`, {{ method: "POST", headers }});
        if (response.status === 401) {{
          const token = window.prompt("请输入推送 token");
          if (!token) return;
          sessionStorage.setItem("inktimePushToken", token);
          response = await fetch(`/api/push/manual/${{renderId}}`, {{
            method: "POST",
            headers: {{ "X-Push-Token": token }}
          }});
        }}
        const data = await response.json().catch(() => ({{ ok: false, error: "响应解析失败" }}));
        if (!response.ok || !data.ok) {{
          window.alert(`推送失败：${{data.error || response.statusText}}`);
          return;
        }}
        window.alert(`已推送，墨水屏可下载 ${{data.image_url}}\\n浏览器预览 ${{data.preview_url}}`);
      }};
      window.addEventListener("load", syncDetailPanels);
      window.addEventListener("resize", syncDetailPanels);
      if ("ResizeObserver" in window) {{
        document.querySelectorAll(".preview-panel").forEach((panel) => {{
          new ResizeObserver(syncDetailPanels).observe(panel);
        }});
      }}
      syncDetailPanels();
    }})();
  </script>
</body>
</html>"""


def _render_renders_page(manifest: dict[str, Any]) -> str:
    renders = manifest.get("renders", [])
    width = manifest.get("final_width") or manifest.get("width", 800)
    height = manifest.get("final_height") or manifest.get("height", 480)
    mode = manifest.get("mode", "scale")
    dither = manifest.get("dither", "atkinson")
    generated_at = manifest.get("generated_at", "")
    body: list[str] = [
        f"""
        <section class="hero">
          <div>
            <h1>本地渲染画廊</h1>
            <p class="lead">查看 AI 分析后的照片，以及按 PhotoPainter E6 六色算法生成的本地预览成品。</p>
          </div>
          <div class="stats">
            <span class="chip">{len(renders)} 张</span>
            <span class="chip">{_esc(width)}x{_esc(height)}</span>
            <span class="chip">模式 {_esc(mode)}</span>
            <span class="chip">抖动 {_esc(dither)}</span>
          </div>
        </section>
        """
    ]
    if generated_at:
        body.append(f'<p class="small">生成时间：{_esc(generated_at)}</p>')
    if not renders:
        body.append(
            '<div class="empty">还没有渲染成品。请先运行 <code>python render_photopainter.py</code>。</div>'
        )
        return "".join(body)

    body.append('<section class="gallery-grid">')
    for idx, item in enumerate(renders):
        render_png = str(item.get("render_png", ""))
        side = _display_side_caption(item)
        caption = str(item.get("caption") or "")
        source_path = _short_path(item.get("source_path"))
        city = _display_location(item)
        date = str(item.get("exif_date") or "")
        type_text = str(item.get("type") or "未分类")
        analysis_channel = str(item.get("analysis_channel") or "")
        memory = _fmt_score(item.get("memory_score"))
        beauty = _fmt_score(item.get("beauty_score"))
        meta = " · ".join(part for part in [date, city, type_text, analysis_channel] if part)
        body.append(
            f"""
            <a class="render-card" href="/renders/{idx}">
              <img class="thumb" src="/static/renders/{_esc(render_png)}" alt="{_esc(render_png)}">
              <div class="card-copy">
                <p class="caption">{_esc(side)}</p>
                <p class="small">{_esc(caption)}</p>
                <p class="small">{_esc(source_path)}</p>
                <p class="small">{_esc(meta)}</p>
                <div class="score-line">
                  <span>回忆度: {_esc(memory)}</span>
                  <span>美观度: {_esc(beauty)}</span>
                </div>
              </div>
            </a>
            """
        )
    body.append("</section>")
    return "".join(body)


def _render_detail_page(manifest: dict[str, Any], item: dict[str, Any], item_id: int) -> str:
    render_png = str(item.get("render_png", ""))
    render_bmp = str(item.get("render_bmp", ""))
    side = _display_side_caption(item)
    caption = str(item.get("caption") or "")
    reason = str(item.get("reason") or "暂无评分理由。")
    source_path = str(item.get("source_path") or "")
    analysis_channel = str(item.get("analysis_channel") or "")
    analysis_model = str(item.get("analysis_model") or "")
    crop_focus = item.get("crop_focus")
    crop_focus_text = json.dumps(crop_focus, ensure_ascii=False) if crop_focus else "-"
    location = _display_location(item)
    location_html = (
        f'<span class="paper-location"><img src="/static/location.svg" alt="" aria-hidden="true"><span>{_esc(location)}</span></span>'
        if location
        else ""
    )
    image_width = manifest.get("width", 800)
    image_height = manifest.get("height", 432)
    final_width = manifest.get("final_width") or image_width
    final_height = manifest.get("final_height") or 480
    mode = manifest.get("mode", "scale")
    dither = manifest.get("dither", "atkinson")
    memory = item.get("memory_score")
    beauty = item.get("beauty_score")
    meta_rows = [
        ("原图路径", source_path),
        ("EXIF 日期", item.get("exif_date") or "-"),
        ("城市/地点", _display_location(item) or "-"),
        ("渲染文件", render_png),
        ("BMP 文件", render_bmp or "-"),
        ("最终尺寸", f"{final_width}x{final_height}（含文字条）"),
        ("图像区", f"{image_width}x{image_height}"),
        ("渲染参数", f"{mode} / {dither}"),
        ("分析通道", analysis_channel or "-"),
        ("分析模型", analysis_model or "-"),
        ("裁切关注区", crop_focus_text),
    ]
    detail_items = "".join(
        f"<div><strong>{_esc(label)}：</strong>{_esc(value)}</div>" for label, value in meta_rows
    )
    return f"""
    <section class="hero">
      <div>
        <h1>渲染效果预览</h1>
        <p class="lead">单张照片的六色渲染结果、AI 描述和评分理由。</p>
      </div>
      <div class="stats">
        <a class="button" href="/renders">返回画廊</a>
        <a class="button" href="/render/{item_id}">重新渲染</a>
      </div>
    </section>
    <section class="detail-grid">
      <div class="preview-panel">
        <img src="/static/renders/{_esc(render_png)}" alt="{_esc(render_png)}">
        <div class="paper-caption"><span class="paper-caption-text">{_esc(side)}</span>{location_html}</div>
      </div>
      <aside class="info-panel">
        <h2 class="quote-title">「{_esc(side)}」</h2>
        <div class="pills">{_type_pills(item.get("type"))}{_channel_badge(analysis_channel)}</div>
        <p class="description">{_esc(caption)}</p>
        <div class="score-row">
          <span class="muted">回忆度</span>
          <div class="bar"><span style="--value: {_score_width(memory)}%"></span></div>
          <strong>{_esc(_fmt_score(memory))}</strong>
        </div>
        <div class="score-row">
          <span class="muted">美观度</span>
          <div class="bar"><span style="--value: {_score_width(beauty)}%"></span></div>
          <strong>{_esc(_fmt_score(beauty))}</strong>
        </div>
        <div class="reason">
          <strong>评分理由</strong>
          <div class="muted">{_esc(reason)}</div>
        </div>
        <details>
          <summary>更多信息</summary>
          <div class="detail-list">{detail_items}</div>
        </details>
        <div class="actions">
          <a class="button" href="/source/{item_id}">查看原图</a>
          <a class="button" href="/static/renders/{_esc(render_png)}">打开 PNG</a>
          <button class="button" type="button" onclick="pushRender({item_id})">手动推送</button>
        </div>
      </aside>
    </section>
    """


def _render_review_page(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return '<div class="empty">还没有分析结果。请先运行 <code>python analyze_photos.py -j 1 --debug</code>。</div>'

    body = [
        """
        <section class="hero">
          <div>
            <h1>照片分析结果</h1>
            <p class="lead">按回忆度和美观度排序查看入库结果。</p>
          </div>
          <div class="stats"><span class="chip">最多 200 条</span></div>
        </section>
        <table><thead><tr><th>评分</th><th>文案</th><th>来源</th></tr></thead><tbody>
        """
    ]
    for row in rows:
        score = f"回忆 {_fmt_score(row['memory_score'])}<br>美观 {_fmt_score(row['beauty_score'])}"
        caption = _esc(row["side_caption"] or row["caption"])
        details = _esc(row["reason"])
        source = _esc(row["path"])
        channel = _esc(row["analysis_channel"])
        body.append(
            f"<tr><td>{score}<br><span class='small'>{channel}</span></td><td><strong>{caption}</strong><br><span class='small'>{details}</span></td><td class='small'>{source}</td></tr>"
        )
    body.append("</tbody></table>")
    return "".join(body)


def _render_dashboard_page(status: dict[str, Any]) -> str:
    recent_push = status.get("recent_push") or {}
    preview_url = str(recent_push.get("preview_url") or "")
    recent_time = str(recent_push.get("pushed_at") or "暂无推送")
    recent_caption = str(recent_push.get("side_caption") or recent_push.get("source_path") or "还没有设备成品")
    monitor_badge = "目录可用" if status.get("monitor_dir_exists") else "目录不可用"
    push_panel = (
        f"""
        <aside class="latest-push">
          <div>
            <p class="status-kicker">Latest Push</p>
            <h2>最近推送</h2>
            <p class="muted">{_esc(recent_time)}</p>
          </div>
          <img src="{_esc(preview_url)}" alt="最近推送预览">
          <p class="caption">{_esc(recent_caption)}</p>
          <a class="button" href="/gallery">进入画廊</a>
        </aside>
        """
        if preview_url
        else f"""
        <aside class="latest-push">
          <div>
            <p class="status-kicker">Latest Push</p>
            <h2>最近推送</h2>
            <p class="muted">{_esc(recent_time)}</p>
          </div>
          <div class="empty">暂无最近推送。先从画廊选择一张照片进入推送工作台。</div>
          <a class="button" href="/gallery">进入画廊</a>
        </aside>
        """
    )
    return f"""
    <section class="hero">
      <div>
        <p class="status-kicker">Warmth Archive</p>
        <h1>收集散落的人间暖意，定格每一段时光</h1>
        <p class="lead">状态中控台｜照片瀑布流｜批量预览｜设备推送</p>
      </div>
      <div class="stats">
        <span class="chip">{_esc(monitor_badge)}</span>
        <span class="chip">数据库 {_esc("可用" if status.get("health", {}).get("database_exists") else "未创建")}</span>
      </div>
    </section>
    <section class="dashboard-grid">
      <div>
        <div class="status-panel">
          <p class="status-kicker">Dashboard</p>
          <h2 class="status-title">{_esc(status.get("analyzed_photos"))} 张照片已分析，画廊可以浏览</h2>
          <p class="lead">监控目录：{_esc(status.get("monitor_dir"))}</p>
        </div>
        <div class="metric-grid">
          <div class="metric-card"><span>监控照片</span><strong>{_esc(status.get("monitored_files"))}</strong></div>
          <div class="metric-card"><span>已分析</span><strong>{_esc(status.get("analyzed_photos"))}</strong></div>
          <div class="metric-card"><span>待分析估算</span><strong>{_esc(status.get("unanalyzed_estimate"))}</strong></div>
          <div class="metric-card"><span>缺失标记</span><strong>{_esc(status.get("missing_photos"))}</strong></div>
        </div>
        <div class="dashboard-actions">
          <a class="button primary-button" href="/gallery">打开画廊</a>
          <a class="button" href="/renders">查看旧渲染成品</a>
          <a class="button" href="/review">分析结果</a>
        </div>
      </div>
      {push_panel}
    </section>
    """


def _render_gallery_page(photos: list[dict[str, Any]], *, sort: str, limit: int) -> str:
    sort_options = [
        ("score", "综合分最高"),
        ("date", "拍摄日期新到旧"),
        ("rendered", "最近渲染"),
        ("discovery", "推送选片规则"),
    ]
    options = "".join(
        f'<option value="{_esc(value)}" {"selected" if value == sort else ""}>{_esc(label)}</option>'
        for value, label in sort_options
    )
    cards: list[str] = []
    for photo in photos:
        score = _fmt_score(photo.get("score"))
        meta = " · ".join(
            part
            for part in [
                str(photo.get("exif_date") or ""),
                str(photo.get("exif_city") or ""),
                str(photo.get("type") or "未分类"),
            ]
            if part
        )
        cards.append(
            f"""
            <article class="photo-card" tabindex="0">
              <img src="{_esc(photo.get("source_url"))}" alt="{_esc(photo.get("side_caption"))}" loading="lazy">
              <div class="photo-card-actions">
                <a class="button primary-button" href="/push-studio/{_esc(photo.get("photo_id"))}">加入推送 ↗</a>
              </div>
              <div class="score-badge">{_esc(score)}<small>综合分</small></div>
              <div class="photo-card-copy">
                <p class="caption">{_esc(photo.get("side_caption"))}</p>
                <p class="small">{_esc(meta or "暂无日期/地点")}</p>
                <p class="small"><a href="/photos/{_esc(photo.get("photo_id"))}">查看详情</a></p>
              </div>
            </article>
            """
        )
    if not cards:
        cards.append(
            """
            <div class="empty">
              还没有可展示的已分析照片。可能是数据库为空、照片被标记 missing，或监控目录暂时不可读。
            </div>
            """
        )
    return f"""
    <section class="hero">
      <div>
        <p class="status-kicker">Gallery</p>
        <h1>已分析照片瀑布流</h1>
        <p class="lead">从数据库读取已分析且仍存在于磁盘的照片，不再使用旧 manifest 下标作为身份。</p>
      </div>
      <div class="stats">
        <span class="chip">{len(photos)} 张</span>
        <span class="chip">最多 {limit} 张</span>
      </div>
    </section>
    <form class="gallery-toolbar" method="get" action="/gallery">
      <label class="small">排序
        <select name="sort" onchange="this.form.submit()">{options}</select>
      </label>
      <div>
        <input type="hidden" name="limit" value="{_esc(limit)}">
        <button class="button" type="submit">刷新候选</button>
      </div>
    </form>
    <section class="photo-masonry">{"".join(cards)}</section>
    """


def _render_photo_database_detail(photo: dict[str, Any]) -> str:
    meta = " · ".join(
        part
        for part in [
            str(photo.get("exif_date") or ""),
            str(photo.get("exif_city") or ""),
            str(photo.get("type") or "未分类"),
            str(photo.get("analysis_channel") or ""),
        ]
        if part
    )
    return f"""
    <section class="hero">
      <div>
        <p class="status-kicker">Photo #{_esc(photo.get("photo_id"))}</p>
        <h1>{_esc(photo.get("side_caption") or "未命名照片")}</h1>
        <p class="lead">{_esc(meta or "暂无日期/地点")}</p>
      </div>
      <div class="stats">
        <a class="button" href="/gallery">返回画廊</a>
        <a class="button primary-button" href="/push-studio/{_esc(photo.get("photo_id"))}">进入推送工作台</a>
      </div>
    </section>
    <section class="detail-grid">
      <div class="preview-panel">
        <img src="{_esc(photo.get("source_url"))}" alt="{_esc(photo.get("side_caption"))}">
        <div class="paper-caption"><span class="paper-caption-text">{_esc(photo.get("side_caption"))}</span></div>
      </div>
      <aside class="info-panel">
        <h2>AI 分析</h2>
        <p class="description">{_esc(photo.get("caption"))}</p>
        <div class="score-row">
          <span class="muted">回忆度</span>
          <div class="bar"><span style="--value: {_score_width(photo.get("memory_score"))}%"></span></div>
          <strong>{_esc(_fmt_score(photo.get("memory_score")))}</strong>
        </div>
        <div class="score-row">
          <span class="muted">美观度</span>
          <div class="bar"><span style="--value: {_score_width(photo.get("beauty_score"))}%"></span></div>
          <strong>{_esc(_fmt_score(photo.get("beauty_score")))}</strong>
        </div>
        <div class="reason">
          <strong>评分理由</strong>
          <div class="muted">{_esc(photo.get("reason"))}</div>
        </div>
      </aside>
    </section>
    """


def _render_push_studio_placeholder(photo: dict[str, Any]) -> str:
    return f"""
    <section class="hero">
      <div>
        <p class="status-kicker">Push Studio</p>
        <h1>{_esc(photo.get("side_caption") or "单张推送工作台")}</h1>
        <p class="lead">完整构图、文案微调和设备一致预览将在后续任务实现；当前入口已按 photo_id 对齐。</p>
      </div>
      <div class="stats">
        <a class="button" href="/gallery">返回画廊</a>
      </div>
    </section>
    <section class="detail-grid">
      <div class="preview-panel">
        <img src="{_esc(photo.get("source_url"))}" alt="{_esc(photo.get("side_caption"))}">
        <div class="paper-caption"><span class="paper-caption-text">{_esc(photo.get("side_caption"))}</span></div>
      </div>
      <aside class="info-panel">
        <h2>推送准备</h2>
        <p class="description">这张照片已经可以作为后续推送工作台的输入。</p>
        <div class="actions">
          <a class="button" href="/photos/{_esc(photo.get("photo_id"))}">查看详情</a>
          <a class="button" href="/renders">旧渲染成品</a>
        </div>
      </aside>
    </section>
    """


def create_app(
    *,
    db_path: str | Path | None = None,
    render_output_dir: str | Path | None = None,
) -> Flask:
    app = Flask(__name__)
    db = _resolve_path(db_path or _config_value("DB_PATH", "./photos.db"))
    render_dir = _resolve_path(
        render_output_dir or _config_value("RENDER_OUTPUT_DIR", "./output/photopainter")
    )
    push_dir = _resolve_path(_config_value("PUSH_OUTPUT_DIR", "./output/push"))
    ensure_photo_identity_schema(db)
    ensure_push_schema(db)

    @app.get("/")
    def index():
        status = load_status(
            db,
            monitor_dir=_resolve_path(_config_value("IMAGE_DIR", "./sample_photos")),
            push_dir=push_dir,
        )
        return _page("InkTime 状态中控台", _render_dashboard_page(status))

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.get("/api/status")
    def api_status():
        return jsonify(
            load_status(
                db,
                monitor_dir=_resolve_path(_config_value("IMAGE_DIR", "./sample_photos")),
                push_dir=push_dir,
            )
        )

    @app.get("/gallery")
    def gallery():
        sort = str(request.args.get("sort", "score"))
        limit = min(200, max(1, int(request.args.get("limit", 60))))
        photos = load_photos(
            db,
            limit=limit,
            sort=sort,
            random_seed=str(request.args.get("seed") or ""),
        )
        return _page("InkTime 画廊", _render_gallery_page(photos, sort=sort, limit=limit))

    @app.get("/api/photos")
    def api_photos():
        sort = str(request.args.get("sort", "score"))
        limit = min(200, max(1, int(request.args.get("limit", 60))))
        include_missing = request.args.get("include_missing") in {"1", "true", "yes"}
        return jsonify(
            {
                "ok": True,
                "photos": load_photos(
                    db,
                    limit=limit,
                    sort=sort,
                    include_missing=include_missing,
                    random_seed=str(request.args.get("seed") or ""),
                ),
            }
        )

    @app.get("/api/photos/<int:photo_id>/source")
    def photo_source(photo_id: int):
        photo = load_photo(db, photo_id)
        if photo is None or not photo.get("exists_on_disk"):
            abort(404)
        target = Path(str(photo.get("path") or "")).expanduser()
        if not target.exists() or not target.is_file():
            abort(404)
        return send_file(target)

    @app.get("/photos/<int:photo_id>")
    def photo_detail(photo_id: int):
        photo = load_photo(db, photo_id)
        if photo is None or not photo.get("exists_on_disk"):
            abort(404)
        return _page("照片详情", _render_photo_database_detail(photo))

    @app.get("/push-studio/<int:photo_id>")
    def push_studio(photo_id: int):
        photo = load_photo(db, photo_id)
        if photo is None or not photo.get("exists_on_disk"):
            abort(404)
        return _page("单张推送工作台", _render_push_studio_placeholder(photo))

    @app.get("/renders")
    def renders():
        manifest = _load_manifest(render_dir)
        return _page("PhotoPainter 渲染成品", _render_renders_page(manifest))

    @app.get("/renders/<int:item_id>")
    def render_detail(item_id: int):
        manifest = _load_manifest(render_dir)
        renders = manifest.get("renders", [])
        if item_id < 0 or item_id >= len(renders):
            abort(404)
        return _page("渲染效果预览", _render_detail_page(manifest, renders[item_id], item_id))

    @app.get("/review")
    def review():
        rows = _read_review_rows(db)
        return _page("照片分析结果", _render_review_page(rows))

    @app.get("/static/renders/<path:filename>")
    def render_static(filename: str):
        target = (render_dir / filename).resolve()
        if render_dir not in target.parents and target != render_dir:
            abort(404)
        if not target.exists() or not target.is_file():
            abort(404)
        return send_file(target)

    @app.get("/push/<path:filename>")
    def push_static(filename: str):
        allowed = {"latest.bmp", "latest.png", "manifest.json"}
        if filename not in allowed:
            abort(404)
        target = (push_dir / filename).resolve()
        if push_dir not in target.parents and target != push_dir:
            abort(404)
        if not target.exists() or not target.is_file():
            abort(404)
        return send_file(target)

    @app.get("/source/<int:item_id>")
    def source(item_id: int):
        manifest = _load_manifest(render_dir)
        renders = manifest.get("renders", [])
        if item_id < 0 or item_id >= len(renders):
            abort(404)
        target = Path(str(renders[item_id].get("source_path", ""))).expanduser()
        if not target.exists() or not target.is_file():
            abort(404)
        return send_file(target)

    @app.get("/render/<int:item_id>")
    def rerender(item_id: int):
        limit = item_id + 1
        render_from_database(
            db_path=db,
            output_dir=render_dir,
            limit=limit,
            width=int(request.args.get("width", _config_value("RENDER_WIDTH", 800))),
            height=int(request.args.get("height", _config_value("RENDER_HEIGHT", 432))),
            final_height=int(_config_value("FINAL_RENDER_HEIGHT", 480)),
            caption_height=int(_config_value("CAPTION_BAR_HEIGHT", 48)),
            mode=str(request.args.get("mode", _config_value("RENDER_MODE", "scale"))),
            dither=str(request.args.get("dither", _config_value("DITHER_MODE", "atkinson"))),
            brightness=float(request.args.get("brightness", _config_value("BRIGHTNESS", 1.1))),
            contrast=float(request.args.get("contrast", _config_value("CONTRAST", 1.2))),
            saturation=float(request.args.get("saturation", _config_value("SATURATION", 1.2))),
            save_bmp=bool(_config_value("SAVE_BMP_OUTPUT", True)),
            font_path=str(_config_value("FONT_PATH", "")),
        )
        return redirect(f"/renders/{item_id}")

    @app.post("/api/push/manual/<int:item_id>")
    def push_manual(item_id: int):
        token = str(_config_value("PUSH_API_TOKEN", "") or "")
        if token and request.headers.get("X-Push-Token", "") != token:
            return jsonify({"ok": False, "error": "推送 token 错误或缺失"}), 401
        settings = PushSettings(
            db_path=db,
            render_output_dir=render_dir,
            push_output_dir=push_dir,
            width=int(_config_value("RENDER_WIDTH", 800)),
            image_height=int(_config_value("RENDER_HEIGHT", 432)),
            final_height=int(_config_value("FINAL_RENDER_HEIGHT", 480)),
            caption_height=int(_config_value("CAPTION_BAR_HEIGHT", 48)),
            mode=str(_config_value("RENDER_MODE", "scale")),
            dither=str(_config_value("DITHER_MODE", "atkinson")),
            brightness=float(_config_value("BRIGHTNESS", 1.1)),
            contrast=float(_config_value("CONTRAST", 1.2)),
            saturation=float(_config_value("SATURATION", 1.2)),
            font_path=str(_config_value("FONT_PATH", "")) or None,
            timezone=str(_config_value("PUSH_TIMEZONE", "Asia/Shanghai")),
            exclude_days=int(_config_value("PUSH_EXCLUDE_DAYS", 90)),
        )
        try:
            manifest = publish_render(
                item_id,
                settings=settings,
                trigger_type="manual",
            )
        except IndexError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except FileNotFoundError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 404
        except Exception as exc:
            return jsonify({"ok": False, "error": f"BMP 生成失败：{exc}"}), 500
        return jsonify({"ok": True, **manifest})

    return app


app = create_app()


if __name__ == "__main__":
    host = str(_config_value("FLASK_HOST", "127.0.0.1"))
    port = int(_config_value("FLASK_PORT", 8765))
    app.run(host=host, port=port, debug=False)
