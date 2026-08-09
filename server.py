#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""纯软件版 PhotoPainter 本地预览服务。"""

from __future__ import annotations

import atexit
import datetime as dt
import html
import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import click
from flask import Flask, abort, g, jsonify, redirect, request, send_file, session
from asset_maintenance import AssetMaintenance

from analysis_tasks import AnalysisTaskError, AnalysisTaskService
from analysis_worker import AnalysisWorker, AnalysisWorkerRunner, LegacyAnalysisExecutor
from photo_identity import ensure_photo_identity_schema
from model_provider import ModelProviderClient
from library_scanner import LibraryScanner, PeriodicScanScheduler, ScanCoordinator
from push_manager import (
    PushSettings,
    ensure_push_schema,
    normalize_render_overrides,
    publish_render,
    settings_from_config,
    timezone_from_name,
    write_latest_files,
)
from render_photopainter import render_from_database
from web_queries import (
    load_library_assets,
    load_library_source,
    load_photo,
    load_photos,
    load_status,
)
from web_auth import WebAuth
from settings_store import MasterKeyUnavailable, SettingsError, SettingsStore


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


def _json_attr(value: Any) -> str:
    return html.escape(json.dumps(value, ensure_ascii=False), quote=True)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _short_path(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    path = Path(text)
    parts = path.parts
    if len(parts) >= 3:
        return str(Path(*parts[-3:]))
    return text


def _safe_next_url(value: Any, default: str = "/") -> str:
    candidate = str(value or "").strip()
    parsed = urlsplit(candidate)
    if not candidate.startswith("/") or candidate.startswith("//") or parsed.netloc:
        return default
    return candidate


def _public_photo(photo: dict[str, Any]) -> dict[str, Any]:
    photo_id = int(photo["photo_id"])
    hidden = {"path", "preview_png_path", "bmp_path"}
    public = {key: value for key, value in photo.items() if key not in hidden}
    public["filename"] = Path(str(photo.get("path") or "")).name
    public["source_url"] = f"/media/previews/{photo_id}.jpg"
    return public


def _photo_api_response(photo: dict[str, Any]) -> dict[str, Any]:
    response = _public_photo(photo)
    if getattr(g, "is_admin", False):
        response["source_url"] = str(photo.get("source_url") or "")
    return response


def _safe_manifest_response(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _safe_manifest_response(item)
            for key, item in value.items()
            if key != "path" and not key.endswith("_path")
        }
    if isinstance(value, list):
        return [_safe_manifest_response(item) for item in value]
    return value


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


def _page(title: str, body: str, *, active: str = "dashboard") -> str:
    is_admin = bool(getattr(g, "is_admin", True))
    active_dashboard = "active" if active == "dashboard" else ""
    active_gallery = "active" if active == "gallery" else ""
    active_studio = "active" if active == "studio" else ""
    active_settings = "active" if active == "settings" else ""
    active_library = "active" if active == "library" else ""
    admin_navigation = f"""
        <a class="{active_dashboard}" href="/">
          <span class="nav-icon">⌂</span>
          <span>中控台</span>
        </a>
        <a class="{active_studio}" href="/push-studio">
          <span class="nav-icon">□</span>
          <span>推送工作台</span>
        </a>
        <a class="{active_library}" href="/library">
          <span class="nav-icon">▤</span>
          <span>素材库</span>
        </a>
        <a class="{active_settings}" href="/settings">
          <span class="nav-icon">◌</span>
          <span>设置</span>
        </a>
    """ if is_admin else ""
    csrf_token = str(session.get("csrf_token") or "")
    session_action = (
        f"""
        <a href="/change-password"><span class="nav-icon">◎</span><span>账户</span></a>
        <form class="nav-form" method="post" action="/logout">
          <input type="hidden" name="csrf_token" value="{_esc(csrf_token)}">
          <button type="submit"><span class="nav-icon">↪</span><span>退出登录</span></button>
        </form>
        """
        if is_admin
        else '<a href="/login"><span class="nav-icon">◎</span><span>管理员登录</span></a>'
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="csrf-token" content="{_esc(csrf_token)}">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <div class="app-shell">
    <aside class="rail" aria-label="主导航">
      <a class="mark" href="/" aria-label="InkTime PhotoPainter">
        <span class="mark-dot"></span>
        <span>InkTime</span>
      </a>
      <nav class="rail-nav">
        {admin_navigation}
        <a class="{active_gallery}" href="/gallery">
          <span class="nav-icon">▦</span>
          <span>画廊</span>
        </a>
        {session_action}
      </nav>
      <div class="rail-note">
        <span class="status-dot"></span>
        本地 WebUI<br>
        真实数据驱动
      </div>
    </aside>
    <main class="workspace">
      <header class="topbar">
        <div class="hero-title">
          <p class="hero-script">Warmth Archive</p>
          <h1>收集散落的人间暖意，定格每一段时光</h1>
          <p class="hero-subline">照片瀑布流｜批量预览｜设备推送</p>
        </div>
      </header>
      {body}
    </main>
  </div>
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
        const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
        if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
        const savedToken = sessionStorage.getItem("inktimePushToken");
        if (savedToken) headers["X-Push-Token"] = savedToken;
        let response = await fetch(`/api/push/manual/${{renderId}}`, {{ method: "POST", headers }});
        if (response.status === 401) {{
          const token = window.prompt("请输入推送 token");
          if (!token) return;
          sessionStorage.setItem("inktimePushToken", token);
          response = await fetch(`/api/push/manual/${{renderId}}`, {{
            method: "POST",
            headers: {{ "X-Push-Token": token, "X-CSRF-Token": csrfToken }}
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
  <script src="/static/app.js"></script>
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
    source_path = _short_path(item.get("source_path"))
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
        source = _esc(_short_path(row["path"]))
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
    recent_caption = str(
        recent_push.get("side_caption")
        or _short_path(recent_push.get("source_path"))
        or "还没有设备成品"
    )
    recent_photo_id = recent_push.get("photo_id")
    recent_studio_href = f"/push-studio/{_esc(recent_photo_id)}" if recent_photo_id else "/push-studio"
    state_label = "分析完成" if status.get("analyzed_photos") else "等待分析"
    state_copy = (
        f"{_esc(status.get('analyzed_photos'))} 张照片已入库，可以进入画廊筛选"
        if status.get("analyzed_photos")
        else "还没有已分析照片，先完成扫描与 AI 分析"
    )
    progress = 100
    if status.get("monitored_files"):
        progress = round((int(status.get("analyzed_photos") or 0) / int(status["monitored_files"])) * 100)
        progress = max(0, min(100, progress))
    latest_body = (
        f"""
        <div class="push-preview">
          <img src="{_esc(preview_url)}" alt="最近推送的六色预览">
          <div>
            <strong>{_esc(recent_caption)}</strong>
            <p>{_esc(recent_time)}</p>
            <a class="ghost-button compact" href="{recent_studio_href}">调整这张图</a>
          </div>
        </div>
        """
        if preview_url
        else '<div class="empty">暂无最近推送。先从画廊选择一张照片进入推送工作台。</div>'
    )
    return f"""
    <section class="screen dashboard-screen" aria-labelledby="dashboard-title">
      <div class="section-heading">
        <div>
          <p class="kicker">Dashboard</p>
          <h2 id="dashboard-title">状态中控台</h2>
        </div>
        <div class="directory-chip" title="监控目录">{_esc(status.get("monitor_dir"))}</div>
      </div>

      <section class="status-band">
        <div class="status-copy">
          <span class="state-label good">{_esc(state_label)}</span>
          <h3>{state_copy}</h3>
          <p>本地模型可用时优先使用；失败后自动切换云端通道。缺失照片只标记，不自动删除。</p>
        </div>
        <div class="status-meter" aria-label="分析进度">
          <div class="meter-head">
            <span>分析进度</span>
            <strong>{progress}%</strong>
          </div>
          <div class="meter"><span style="width: {progress}%"></span></div>
        </div>
      </section>

      <section class="metric-grid" aria-label="照片库统计">
        <article class="metric-tile">
          <span>监控照片</span>
          <strong>{_esc(status.get("monitored_files"))}</strong>
          <small>当前目录可用于 AI 计算</small>
        </article>
        <article class="metric-tile">
          <span>已分析</span>
          <strong>{_esc(status.get("analyzed_photos"))}</strong>
          <small>已写入数据库</small>
        </article>
        <article class="metric-tile">
          <span>待分析估算</span>
          <strong>{_esc(status.get("unanalyzed_estimate"))}</strong>
          <small>按监控目录估算</small>
        </article>
        <article class="metric-tile warning">
          <span>缺失标记</span>
          <strong>{_esc(status.get("missing_photos"))}</strong>
          <small>等待人工清理</small>
        </article>
      </section>

      <section class="dashboard-grid">
        <div class="action-dock" aria-label="快捷操作">
          <button class="dock-action primary" type="button" disabled title="分析任务控制将在后续阶段接入">开始 / 暂停分析</button>
          <form method="post" action="/api/library/scan">
            <input type="hidden" name="csrf_token" value="{_esc(session.get('csrf_token') or '')}">
            <button class="dock-action" type="submit">重新扫描照片库</button>
          </form>
          <button class="dock-action" type="button" disabled title="分析任务控制将在后续阶段接入">停止分析</button>
          <a class="dock-action" href="/gallery">进入画廊</a>
          <a class="dock-action" href="/settings">模型设置</a>
        </div>

        <article class="latest-push" aria-label="最近推送">
          <div class="panel-head">
            <div>
              <p class="kicker">Latest Push</p>
              <h3>最近推送</h3>
            </div>
            <span class="state-label">{_esc(recent_push.get("trigger_type") or "无记录")}</span>
          </div>
          {latest_body}
        </article>

        <article class="log-panel" aria-label="运行日志">
          <div class="panel-head">
            <div>
              <p class="kicker">Log</p>
              <h3>运行日志</h3>
            </div>
            <button class="text-button" type="button" data-log-toggle aria-expanded="true" aria-controls="dashboard-log-list">折叠</button>
          </div>
          <ol class="log-list" id="dashboard-log-list">
            <li><time>就绪</time><span>本地 WebUI 已启动。</span></li>
            <li><time>数据库</time><span>已分析 {_esc(status.get("analyzed_photos"))} 张照片。</span></li>
            <li><time>目录</time><span>发现 {_esc(status.get("monitored_files"))} 个可用图片文件。</span></li>
            <li><time>缺失</time><span>{_esc(status.get("missing_photos"))} 张照片被标记 missing。</span></li>
          </ol>
        </article>
      </section>
    </section>
    """


def _render_library_page(payload: dict[str, Any], filters: dict[str, str]) -> str:
    summary = payload["summary"]
    def selected(name: str, value: str) -> str:
        return " selected" if filters.get(name, "") == value else ""

    rows = []
    for item in payload["items"]:
        rows.append(
            f"""
            <tr>
              <td><input type="checkbox" name="photo_ids" form="asset-archive-form" aria-label="选择 {_esc(item['filename'])}" data-library-photo value="{_esc(item['photo_id'])}"></td>
              <td><img class="asset-thumb" src="{_esc(item['preview_url'])}" alt=""></td>
              <td><strong>{_esc(item['filename'])}</strong><small>{_esc(item['directory'] or '根目录')}</small></td>
              <td><span class="state-label">{_esc(item['file_status'])}</span></td>
              <td>{'已归档' if item['visibility_status'] == 'archived' else '显示中'}</td>
              <td>{_esc(item['analysis_status'])}</td>
              <td>{_esc(item['captured_at'] or '-')}</td>
              <td>{'有' if item['has_gps'] else '无'}</td>
              <td>{_esc(item['type'] or '-')}</td>
              <td>{_esc(item['size_bytes'])}</td>
            </tr>
            """
        )
    body = "".join(rows) or '<tr><td colspan="10" class="empty">当前筛选没有素材。</td></tr>'
    selection_context = {
        "filters": {key: value for key, value in filters.items() if key not in {"sort", "order"} and value},
        "sort": filters.get("sort") or "created_at",
        "order": filters.get("order") or "desc",
    }
    return f"""
    <section class="screen library-screen" aria-labelledby="library-title" data-library-selection data-selection-context="{_esc(json.dumps(selection_context, ensure_ascii=False))}">
      <div class="section-heading">
        <div><p class="kicker">Asset Library</p><h2 id="library-title">素材库</h2></div>
        <form method="post" action="/api/library/scan">
          <input type="hidden" name="csrf_token" value="{_esc(session.get('csrf_token') or '')}">
          <button class="primary-button" type="submit">重新扫描</button>
        </form>
      </div>
      <div class="metric-grid library-metrics">
        <article class="metric-tile"><span>全部素材</span><strong>{_esc(str(summary['total']))}</strong></article>
        <article class="metric-tile"><span>可分析</span><strong>{_esc(str(summary['analyzable']))}</strong></article>
        <article class="metric-tile"><span>当前结果</span><strong>{_esc(str(payload['filtered_total']))}</strong></article>
      </div>
      <form class="library-filters" method="get">
        <label>文件状态<select name="file_status"><option value="">全部</option><option value="present"{selected('file_status', 'present')}>可读</option><option value="unreadable"{selected('file_status', 'unreadable')}>不可读</option><option value="missing"{selected('file_status', 'missing')}>缺失</option></select></label>
        <label>分析状态<select name="analysis_status"><option value="">全部</option><option value="pending"{selected('analysis_status', 'pending')}>未分析</option><option value="analyzed"{selected('analysis_status', 'analyzed')}>已分析</option></select></label>
        <label>显示状态<select name="visibility_status"><option value="">全部</option><option value="active"{selected('visibility_status', 'active')}>显示中</option><option value="archived"{selected('visibility_status', 'archived')}>已归档</option></select></label>
        <label>拍摄日期从<input type="date" name="captured_from" value="{_esc(filters.get('captured_from'))}"></label>
        <label>至<input type="date" name="captured_to" value="{_esc(filters.get('captured_to'))}"></label>
        <label>GPS<select name="has_gps"><option value="">全部</option><option value="1"{selected('has_gps', '1')}>有 GPS</option><option value="0"{selected('has_gps', '0')}>无 GPS</option></select></label>
        <label>文件类型<select name="file_type"><option value="">全部</option><option value="jpg"{selected('file_type', 'jpg')}>JPG</option><option value="jpeg"{selected('file_type', 'jpeg')}>JPEG</option><option value="png"{selected('file_type', 'png')}>PNG</option><option value="webp"{selected('file_type', 'webp')}>WebP</option><option value="heic"{selected('file_type', 'heic')}>HEIC</option><option value="heif"{selected('file_type', 'heif')}>HEIF</option></select></label>
        <label>AI 类型<input name="type" value="{_esc(filters.get('type'))}"></label>
        <label>目录<input name="directory" value="{_esc(filters.get('directory'))}"></label>
        <label>文件名<input name="filename" value="{_esc(filters.get('filename'))}"></label>
        <label>排序<select name="sort"><option value="created_at"{selected('sort', 'created_at')}>入库时间</option><option value="captured_at"{selected('sort', 'captured_at')}>拍摄日期</option><option value="filename"{selected('sort', 'filename')}>文件名</option><option value="size"{selected('sort', 'size')}>文件大小</option><option value="random"{selected('sort', 'random')}>随机发现</option></select></label>
        <label>方向<select name="order"><option value="desc"{selected('order', 'desc')}>新到旧 / 大到小</option><option value="asc"{selected('order', 'asc')}>旧到新 / 小到大</option></select></label>
        <button class="ghost-button" type="submit">应用筛选</button>
      </form>
      <form id="asset-archive-form" method="post" action="/api/library/archive">
        <input type="hidden" name="csrf_token" value="{_esc(session.get('csrf_token') or '')}">
      </form>
      <div class="library-selection-bar" aria-label="素材选择操作">
        <strong><span data-selection-count>0</span> 张已选择</strong>
        <button class="button" type="button" data-select-filtered>全选当前筛选</button>
        <label>选择前 <input type="number" min="1" value="20" data-select-limit> 张</label>
        <button class="button" type="button" data-select-top>选择</button>
        <button class="text-button" type="button" data-selection-clear>清空</button>
        <span class="save-state" data-selection-state aria-live="polite"></span>
        <button class="ghost-button" type="submit" form="asset-archive-form" name="archived" value="true">归档所选</button>
        <button class="ghost-button" type="submit" form="asset-archive-form" name="archived" value="false">恢复显示</button>
        <button class="primary-button" type="button" data-task-open>创建分析任务</button>
      </div>
      <div class="asset-table-wrap"><table class="asset-table"><thead><tr><th><span class="visually-hidden">选择</span></th><th>预览</th><th>文件</th><th>文件状态</th><th>显示状态</th><th>分析状态</th><th>拍摄日期</th><th>GPS</th><th>AI 类型</th><th>大小</th></tr></thead><tbody>{body}</tbody></table></div>
      <dialog class="analysis-task-dialog" data-analysis-task-dialog>
        <form method="dialog" class="analysis-task-dialog-card" data-analysis-task-form>
          <div class="dialog-head"><div><p class="kicker">Analysis Task</p><h3>创建分析任务</h3></div><button class="icon-button" value="cancel" aria-label="关闭">×</button></div>
          <div class="task-mode-control" role="group" aria-label="分析模式"><label><input type="radio" name="task_type" value="incremental" checked> 增量分析</label><label><input type="radio" name="task_type" value="reanalysis"> 重新分析</label></div>
          <label class="field-stack"><span>任务名称（可选）</span><input name="name" placeholder="留空自动命名"></label>
          <label class="field-stack"><span>任务内并发</span><input name="concurrency" type="number" min="1" max="4" value="1"></label>
          <div class="task-confirm-summary" data-task-summary>正在核对素材资格…</div>
          <label class="high-cost-confirm" data-high-cost hidden><input type="checkbox" name="confirmed_high_cost"> 我已确认本次分析数量和可能产生的模型费用</label>
          <p class="save-state" data-task-state aria-live="polite"></p>
          <div class="dialog-actions"><button class="button" value="cancel">取消</button><button class="primary-button" type="submit" value="default">创建任务</button></div>
        </form>
      </dialog>
    </section>
    """


def _render_analysis_task_page(task: dict[str, Any]) -> str:
    mode = "重新分析" if task["task_type"] == "reanalysis" else "增量分析"
    progress_heading = "任务已进入队列" if task["status"] == "queued" else "任务执行进度"
    levels = "".join(
        f"<li><span>{index + 1}</span><strong>{_esc(item['channel_name'])}</strong><small>{_esc(item['model_id'])} · v{_esc(item['channel_version'])}</small></li>"
        for index, item in enumerate(task["strategy"].get("execution_levels") or [])
    )
    return f"""
    <section class="screen analysis-task-screen" aria-labelledby="analysis-task-title">
      <div class="section-heading"><div><p class="kicker">Analysis Task</p><h2 id="analysis-task-title">{_esc(task['name'])}</h2></div><a class="button" href="/library">返回素材库</a></div>
      <section class="task-created-panel">
        <div><span class="state-label">{_esc(task['status'])}</span><h3>{_esc(progress_heading)}</h3><p>{_esc(mode)} · {_esc(str(task['total_count']))} 张 · 并发 {_esc(str(task['concurrency']))} · 当前 {_esc(task.get('current_filename') or '等待领取')}</p></div>
        <dl><div><dt>已处理</dt><dd>{_esc(str(task['processed_count']))}</dd></div><div><dt>成功</dt><dd>{_esc(str(task['succeeded_count']))}</dd></div><div><dt>失败</dt><dd>{_esc(str(task['failed_count']))}</dd></div><div><dt>剩余</dt><dd>{_esc(str(task['remaining_count']))}</dd></div></dl>
      </section>
      <section class="task-strategy-panel"><p class="kicker">Frozen Strategy</p><h3>模型执行策略</h3><ol>{levels}</ol><p>该任务已冻结素材集合和非敏感模型配置；凭据将在 Worker 执行时读取。</p></section>
    </section>
    """


def _render_gallery_page(photos: list[dict[str, Any]], *, sort: str, limit: int) -> str:
    can_manage = bool(getattr(g, "is_admin", True))
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
    modals: list[str] = []
    photo_ids = [int(photo["photo_id"]) for photo in photos if photo.get("photo_id") is not None]
    for index, photo in enumerate(photos):
        photo_id = int(photo.get("photo_id"))
        score = _fmt_score(photo.get("score"))
        memory = _fmt_score(photo.get("memory_score"))
        beauty = _fmt_score(photo.get("beauty_score"))
        meta = " · ".join(
            part
            for part in [
                str(photo.get("exif_date") or ""),
                str(photo.get("exif_city") or ""),
                str(photo.get("type") or "未分类"),
            ]
            if part
        )
        meta_items = "".join(
            f"<li><strong>{_esc(label)}</strong><span>{_esc(value)}</span></li>"
            for label, value in [
                ("类型", photo.get("type") or "未分类"),
                ("日期", photo.get("exif_date") or "-"),
                ("地点", photo.get("exif_city") or "-"),
                ("模型", photo.get("analysis_channel") or "-"),
            ]
        )
        prev_id = photo_ids[index - 1] if index > 0 and index - 1 < len(photo_ids) else photo_ids[-1]
        next_id = photo_ids[index + 1] if index + 1 < len(photo_ids) else photo_ids[0]
        shape = " tall" if index % 5 in {0, 4} else " wide" if index % 5 == 2 else ""
        push_float = (
            f'<a class="push-float" href="/push-studio/{_esc(photo_id)}">加入推送</a>'
            if can_manage
            else ""
        )
        push_entry = (
            f'<a class="primary-button push-entry" href="/push-studio/{_esc(photo_id)}">进入推送工作台</a>'
            if can_manage
            else ""
        )
        cards.append(
            f"""
            <article class="photo-card{shape}" tabindex="0">
              {push_float}
              <a class="photo-image-link" href="#photo-{_esc(photo_id)}" aria-label="打开照片详情">
                <img src="{_esc(photo.get("source_url"))}" alt="{_esc(photo.get("side_caption"))}" loading="lazy">
              </a>
              <div class="photo-copy">
                <div class="score-pair"><span>{_esc(score)}</span><small>综合分</small></div>
                <h3>{_esc(photo.get("side_caption"))}</h3>
                <p class="small">{_esc(meta or "暂无日期/地点")}</p>
              </div>
            </article>
            """
        )
        modals.append(
            f"""
            <article class="photo-lightbox" id="photo-{_esc(photo_id)}" role="dialog" aria-modal="true" aria-labelledby="photo-title-{_esc(photo_id)}">
              <a class="lightbox-backdrop" href="#gallery-title" aria-label="关闭详情"></a>
              <div class="lightbox-card">
                <a class="lightbox-close" href="#gallery-title" aria-label="关闭详情">×</a>
                <a class="lightbox-arrow prev" href="#photo-{_esc(prev_id)}" aria-label="上一张">‹</a>
                <a class="lightbox-arrow next" href="#photo-{_esc(next_id)}" aria-label="下一张">›</a>
                <p class="lightbox-label">Photo #{_esc(photo_id)}</p>
                <div class="lightbox-grid">
                  <figure class="lightbox-preview">
                    <img src="{_esc(photo.get("source_url"))}" alt="{_esc(photo.get("side_caption"))}">
                    <figcaption>{_esc(photo.get("side_caption"))}</figcaption>
                    <ul class="detail-meta-strip">{meta_items}</ul>
                  </figure>
                  <aside class="lightbox-analysis">
                    <h2 id="photo-title-{_esc(photo_id)}">AI 分析</h2>
                    <p class="description">{_esc(photo.get("caption"))}</p>
                    <div class="score-row">
                      <span class="muted">回忆度</span>
                      <div class="bar"><span style="--value: {_score_width(photo.get("memory_score"))}%"></span></div>
                      <strong>{_esc(memory)}</strong>
                    </div>
                    <div class="score-row">
                      <span class="muted">美观度</span>
                      <div class="bar"><span style="--value: {_score_width(photo.get("beauty_score"))}%"></span></div>
                      <strong>{_esc(beauty)}</strong>
                    </div>
                    <div class="reason">
                      <strong>评分理由</strong>
                      <div class="muted">{_esc(photo.get("reason"))}</div>
                    </div>
                    {push_entry}
                  </aside>
                </div>
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
    <section class="screen gallery-screen" aria-labelledby="gallery-title">
      <div class="section-heading sticky-heading">
        <div>
          <p class="kicker">Gallery</p>
          <h2 id="gallery-title">已分析照片瀑布流</h2>
        </div>
        <form class="toolbar" method="get" action="/gallery">
          <label>
            <span>排序</span>
            <select name="sort" onchange="this.form.submit()">{options}</select>
          </label>
          <input type="hidden" name="limit" value="{_esc(limit)}">
          <button class="ghost-button" type="submit">刷新候选</button>
        </form>
      </div>
      <section class="masonry" aria-label="照片列表">{"".join(cards)}</section>
      {"".join(modals)}
    </section>
    """


def _render_photo_database_detail(photo: dict[str, Any]) -> str:
    can_manage = bool(getattr(g, "is_admin", True))
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
    meta_items = "".join(
        f"<li><strong>{_esc(label)}</strong><span>{_esc(value)}</span></li>"
        for label, value in [
            ("类型", photo.get("type") or "未分类"),
            ("日期", photo.get("exif_date") or "-"),
            ("地点", photo.get("exif_city") or "-"),
            ("模型", photo.get("analysis_channel") or "-"),
        ]
    )
    ai_side_caption = str(photo.get("ai_side_caption") or "")
    custom_side_caption = str(photo.get("custom_side_caption") or "")
    push_action = (
        f'<a class="primary-button" href="/push-studio/{_esc(photo.get("photo_id"))}">进入推送工作台</a>'
        if can_manage
        else ""
    )
    caption_editor = (
        f"""
          <section class="detail-caption-editor" data-detail-caption-editor data-save-url="/api/photos/{_esc(photo.get('photo_id'))}/overrides">
            <h3>文案微调</h3>
            <p class="small"><strong>AI 原始短文案</strong><br>{_esc(ai_side_caption or '暂无')}</p>
            <label class="field-stack"><span>人工文案</span><textarea rows="3" data-detail-caption-input>{_esc(custom_side_caption or ai_side_caption)}</textarea></label>
            <div class="detail-caption-actions"><button class="button" type="button" data-detail-caption-save>保存文案</button><span class="small" data-detail-caption-state aria-live="polite">{_esc('已应用人工覆盖' if custom_side_caption else '当前使用 AI 文案')}</span></div>
          </section>
        """
        if can_manage
        else ""
    )
    return f"""
    <section class="screen photo-detail-screen">
      <div class="detail-page-head">
        <span class="status-kicker">Photo #{_esc(photo.get("photo_id"))}</span>
        <div class="actions">
          <a class="button" href="/gallery">返回画廊</a>
          {push_action}
        </div>
      </div>
      <section class="detail-grid">
        <div class="preview-panel">
          <img src="{_esc(photo.get("source_url"))}" alt="{_esc(photo.get("side_caption"))}">
          <div class="paper-caption"><span class="paper-caption-text">{_esc(photo.get("side_caption"))}</span></div>
          <ul class="detail-meta-strip">{meta_items}</ul>
        </div>
        <aside class="info-panel">
          <h2>AI 分析</h2>
          <p class="small">{_esc(meta or "暂无日期/地点")}</p>
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
          {caption_editor}
        </aside>
      </section>
    </section>
    """


def _render_push_studio_placeholder(photo: dict[str, Any]) -> str:
    saved_crop = _json_object(photo.get("manual_crop_json"))
    crop = {
        "scale": 1.0,
        "offset_x": saved_crop.get("offset_x", saved_crop.get("x", 0)),
        "offset_y": saved_crop.get("offset_y", saved_crop.get("y", 0)),
        "rotation": 0,
        "fit_mode": "fill",
        **saved_crop,
    }
    render_overrides = {
        "show_caption": True,
        "show_date": True,
        "show_location": True,
        "frame_orientation": "landscape",
        "dither_enabled": True,
        "dither_type": str(_config_value("DITHER_MODE", "atkinson")),
        "dither_strength": 1.0,
        "brightness": float(_config_value("BRIGHTNESS", 1.1)),
        "contrast": float(_config_value("CONTRAST", 1.2)),
        "saturation": float(_config_value("SATURATION", 1.2)),
        **normalize_render_overrides(photo.get("render_overrides_json")),
    }
    caption = str(photo.get("custom_side_caption") or photo.get("side_caption") or "")
    exif_date = str(photo.get("exif_date") or "")
    exif_city = str(photo.get("exif_city") or "")
    meta = " · ".join(
        part
        for part in [
            f"Photo #{photo.get('photo_id')}",
            str(photo.get("exif_date") or ""),
            str(photo.get("exif_city") or ""),
            str(photo.get("type") or "未分类"),
        ]
        if part
    )
    return f"""
    <section class="screen studio-screen" data-push-studio data-display-defaults-version="2" data-photo-id="{_esc(photo.get("photo_id"))}" data-save-url="/api/photos/{_esc(photo.get("photo_id"))}/overrides" data-push-url="/api/photos/{_esc(photo.get("photo_id"))}/push" data-source-url="{_esc(photo.get("source_url"))}" data-crop="{_json_attr(crop)}" data-render="{_json_attr(render_overrides)}" data-date="{_esc(exif_date)}" data-location="{_esc(exif_city)}">
      <div class="studio-head">
        <div>
        <p class="status-kicker">Push Studio</p>
          <h2>单张推送工作台</h2>
          <p class="small">{_esc(meta)}</p>
        </div>
        <a class="button" href="/gallery">返回画廊</a>
      </div>
      <section class="detail-grid studio-workspace">
      <div class="studio-stage-card">
        <div class="device-editor" tabindex="0" data-editor-stage aria-label="推送构图编辑器">
          <canvas class="editor-canvas" data-editor-canvas width="800" height="480" aria-label="完整设备成品的六色转换预览"></canvas>
          <div class="editor-loading" data-editor-loading>正在载入原图...</div>
          <div class="editor-safe-frame" aria-hidden="true"></div>
        </div>
        <div class="stage-footnote">
          <p class="stage-hint">拖动原图调整位置，滚轮以光标为中心缩放。六色预览直接使用当前构图，不会先裁切原图。</p>
          <span class="preview-mode" data-preview-mode>六色预览</span>
        </div>
      </div>
      <aside class="info-panel studio-controls">
        <div class="studio-control-head">
          <div><p class="status-kicker">Photo Conversion</p><h2>照片转换</h2></div>
          <label class="preview-switch"><input type="checkbox" data-dither-enabled {"checked" if render_overrides.get("dither_enabled") else ""}><span>六色预览</span></label>
        </div>
        <section class="studio-control-section" aria-labelledby="composition-label">
          <h3 id="composition-label">构图</h3>
          <div class="select-row"><span>相框摆放</span><div class="frame-orientation-control" aria-label="相框摆放方向"><button class="button" type="button" data-frame-orientation="landscape">横放</button><button class="button" type="button" data-frame-orientation="portrait">竖放</button></div></div>
          <label class="select-row"><span>适配方式</span><select data-fit-mode><option value="fill" {"selected" if crop.get("fit_mode") == "fill" else ""}>填满画布</option><option value="contain" {"selected" if crop.get("fit_mode") == "contain" else ""}>完整显示</option></select></label>
          <label class="range-row wide-range"><span>缩放</span><input type="range" min="0.1" max="3" step="0.01" value="{_esc(crop.get("scale"))}" data-zoom-input><strong data-zoom-value>1.00</strong></label>
          <div class="position-readout"><span>水平 <strong data-pan-x>0</strong></span><span>垂直 <strong data-pan-y>0</strong></span></div>
          <div class="compact-actions"><button class="button" type="button" data-fit-button>自动适配</button><button class="button" type="button" data-rotate-button>照片旋转 90°</button><button class="button" type="button" data-reset-button>重置</button></div>
        </section>
        <section class="studio-control-section" aria-labelledby="render-label">
          <div class="control-title-row"><h3 id="render-label">六色渲染</h3><button class="text-button" type="button" data-auto-button>自动配置</button></div>
          <label class="select-row"><span>抖动算法</span><select data-dither-type><option value="atkinson" {"selected" if render_overrides.get("dither_type") == "atkinson" else ""}>PhotoPainter Atkinson（推荐）</option><option value="floyd-steinberg" {"selected" if render_overrides.get("dither_type") == "floyd-steinberg" else ""}>Floyd-Steinberg</option><option value="atkinson-standard" {"selected" if render_overrides.get("dither_type") == "atkinson-standard" else ""}>Atkinson（标准）</option><option value="stucki" {"selected" if render_overrides.get("dither_type") == "stucki" else ""}>Stucki</option><option value="jarvis-judice-ninke" {"selected" if render_overrides.get("dither_type") == "jarvis-judice-ninke" else ""}>Jarvis-Judice-Ninke</option></select></label>
          <label class="range-row parameter-range"><span>抖动强度</span><input type="range" min="0" max="5" step="0.1" value="{_esc(render_overrides.get("dither_strength"))}" data-dither-strength><strong data-dither-strength-value>1.0</strong></label>
          <label class="range-row parameter-range"><span>亮度</span><input type="range" min="0.5" max="1.8" step="0.05" value="{_esc(render_overrides.get("brightness"))}" data-brightness><strong data-brightness-value>1.10</strong></label>
          <label class="range-row parameter-range"><span>对比度</span><input type="range" min="0.5" max="2" step="0.05" value="{_esc(render_overrides.get("contrast"))}" data-contrast><strong data-contrast-value>1.20</strong></label>
          <label class="range-row parameter-range"><span>饱和度</span><input type="range" min="0" max="2" step="0.05" value="{_esc(render_overrides.get("saturation"))}" data-saturation><strong data-saturation-value>1.20</strong></label>
        </section>
        <section class="studio-control-section" aria-labelledby="caption-label">
          <h3 id="caption-label">文字排版</h3>
          <label class="field-stack"><span>文案</span><textarea rows="3" data-caption-input>{_esc(caption)}</textarea></label>
          <div class="check-grid" aria-label="显示内容"><label><input type="checkbox" data-toggle-caption {"checked" if render_overrides.get("show_caption") else ""}> 文案</label><label><input type="checkbox" data-toggle-date {"checked" if render_overrides.get("show_date") else ""}> 日期</label><label><input type="checkbox" data-toggle-location {"checked" if render_overrides.get("show_location") else ""}> 地点</label></div>
        </section>
        <div class="studio-primary-actions"><button class="button" type="button" data-save-button>保存参数</button><button class="primary-button" type="button" data-push-button>生成并推送</button></div>
        <p class="save-state" data-save-state aria-live="polite"></p>
      </aside>
      </section>
    </section>
    """


def _render_settings_page() -> str:
    return """
    <section class="screen settings-screen" aria-labelledby="settings-title">
      <div class="section-heading">
        <div>
          <p class="kicker">Settings</p>
          <h2 id="settings-title">系统配置</h2>
        </div>
        <span class="settings-scope-note">修改仅影响新进程/新任务</span>
      </div>
      <div class="settings-app" data-settings-app>
        <div class="settings-tabs" role="tablist" aria-label="配置区域">
          <button class="active" type="button" role="tab" data-settings-tab="channels">模型通道</button>
          <button type="button" role="tab" data-settings-tab="analysis">分析默认值</button>
          <button type="button" role="tab" data-settings-tab="scan">素材扫描</button>
          <button type="button" role="tab" data-settings-tab="security">安全</button>
        </div>
        <section class="settings-panel active" role="tabpanel" data-settings-panel="channels">
          <div class="settings-panel-head"><div><h3>模型通道</h3><p>按降级顺序组合通道与模型。</p></div><button class="primary-button" type="button" data-channel-add>添加通道</button></div>
          <p class="settings-warning" data-settings-warning hidden></p>
          <div class="channel-list" data-channel-list></div>
          <article class="settings-subpanel">
            <div class="settings-panel-head"><div><h3>降级链</h3><p>上方优先，失败后依次尝试。</p></div><div class="settings-head-actions"><button class="button" type="button" data-fallback-add>添加组合</button><button class="primary-button" type="button" data-fallback-save>保存顺序</button></div></div>
            <div class="fallback-list" data-fallback-list></div>
            <p class="save-state" data-fallback-state aria-live="polite"></p>
          </article>
        </section>
        <section class="settings-panel" role="tabpanel" data-settings-panel="analysis">
          <form class="settings-form" data-settings-form="analysis_defaults">
            <h3>分析默认值</h3>
            <div class="settings-field-grid">
              <label>单次任务数量<input name="batch_size" type="number" min="1" value="10"></label>
              <label>任务内并发<input name="concurrency" type="number" min="1" max="4" value="1"></label>
              <label>高费用确认阈值<input name="high_cost_threshold" type="number" min="1" value="50"></label>
              <label>最大请求轮次<input name="max_request_rounds" type="number" min="1" max="2" value="2"></label>
              <label>图像最长边<input name="max_long_edge" type="number" min="256" value="2560"></label>
              <label>提示词方案<select name="prompt_profile"><option value="balanced">均衡</option><option value="memory">回忆优先</option><option value="beauty">美观优先</option></select></label>
            </div>
            <div class="settings-save-row"><button class="primary-button" type="submit">保存分析默认值</button><span class="save-state" data-form-state></span></div>
          </form>
        </section>
        <section class="settings-panel" role="tabpanel" data-settings-panel="scan">
          <form class="settings-form" data-settings-form="scan_settings">
            <h3>素材扫描</h3>
            <div class="settings-field-grid">
              <label>监控目录<input name="image_dir" type="text" autocomplete="off"></label>
              <label>扫描间隔（分钟）<input name="interval_minutes" type="number" min="1" value="30"></label>
              <label class="toggle-field"><input name="include_subdirectories" type="checkbox" checked> 包含子目录</label>
            </div>
            <div class="settings-save-row"><button class="primary-button" type="submit">保存扫描设置</button><span class="save-state" data-form-state></span></div>
          </form>
        </section>
        <section class="settings-panel" role="tabpanel" data-settings-panel="security">
          <form class="settings-form" data-settings-form="security_settings">
            <h3>安全</h3>
            <div class="settings-field-grid">
              <label class="toggle-field"><input name="audit_events" type="checkbox" checked> 记录配置变更事件</label>
              <label class="toggle-field"><input name="mask_paths" type="checkbox" checked> 页面隐藏完整路径</label>
            </div>
            <div class="settings-save-row"><button class="primary-button" type="submit">保存安全设置</button><span class="save-state" data-form-state></span></div>
          </form>
        </section>
      </div>
    </section>
    """


def _render_login_page(*, csrf_token: str, next_url: str, configured: bool) -> str:
    warning = "" if configured else "<p class='auth-warning'>尚未配置初始管理员密码，请先设置服务器环境变量。</p>"
    return f"""
    <section class="auth-screen" aria-labelledby="login-title">
      <form class="auth-panel" method="post" action="/login">
        <p class="eyebrow">Administrator</p>
        <h2 id="login-title">管理员登录</h2>
        <p>画廊可以只读浏览；配置、分析与推送操作需要管理员会话。</p>
        {warning}
        <input type="hidden" name="csrf_token" value="{_esc(csrf_token)}">
        <input type="hidden" name="next" value="{_esc(next_url)}">
        <label>密码<input name="password" type="password" minlength="8" autocomplete="current-password" required></label>
        <button class="primary-button" type="submit">登录</button>
      </form>
    </section>
    """


def _render_change_password_page(*, csrf_token: str) -> str:
    return f"""
    <section class="auth-screen" aria-labelledby="password-title">
      <form class="auth-panel" method="post" action="/change-password">
        <p class="eyebrow">Security</p>
        <h2 id="password-title">修改管理员密码</h2>
        <p>首次登录必须设置新密码。新密码至少 8 个字符，修改后其他会话会立即失效。</p>
        <input type="hidden" name="csrf_token" value="{_esc(csrf_token)}">
        <label>当前密码<input name="current_password" type="password" autocomplete="current-password" required></label>
        <label>新密码<input name="new_password" type="password" minlength="8" autocomplete="new-password" required></label>
        <button class="primary-button" type="submit">保存新密码</button>
      </form>
    </section>
    """


def _dispatch_channel_diagnostic(
    settings_store: SettingsStore,
    provider_client,
    channel_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    if kind not in {"discover", "connection", "vision"}:
        return {"ok": False, "error": "unknown_diagnostic"}, 400
    channel = settings_store.get_channel(channel_id)
    if channel is None:
        return {"ok": False, "error": "not_found"}, 404
    try:
        api_key = settings_store.resolve_credential(channel_id)
    except Exception:
        return {"ok": False, "error": "credential_unavailable"}, 409
    if kind == "discover":
        result = provider_client.discover_models(channel, api_key)
    elif kind == "connection":
        result = provider_client.test_connection(channel, api_key)
    else:
        model_id = str((payload or {}).get("model_id") or "")
        if not model_id:
            return {"ok": False, "error": "model_required"}, 400
        result = provider_client.test_vision(channel, model_id, api_key)
    return result, (200 if result.get("ok") else 502)


def create_app(
    *,
    db_path: str | Path | None = None,
    render_output_dir: str | Path | None = None,
    auth_required: bool | None = None,
    initial_admin_password: str | None = None,
    session_secret: str | None = None,
    auth_cookie_secure: bool | None = None,
    auth_now=None,
    settings_master_key: str | None = None,
    model_provider=None,
    scan_root: str | Path | None = None,
    scan_startup: bool | None = None,
    scan_interval_minutes: float | None = None,
    analysis_worker_enabled: bool | None = None,
    analysis_executor=None,
    display_preview_dir: str | Path | None = None,
) -> Flask:
    app = Flask(__name__)
    auth_enabled = True if auth_required is None else bool(auth_required)
    db = _resolve_path(db_path or _config_value("DB_PATH", "./photos.db"))
    render_dir = _resolve_path(
        render_output_dir or _config_value("RENDER_OUTPUT_DIR", "./output/photopainter")
    )
    push_dir = _resolve_path(_config_value("PUSH_OUTPUT_DIR", "./output/push"))
    preview_dir = _resolve_path(
        display_preview_dir
        or ((db.parent / "previews") if db_path is not None else _config_value("DISPLAY_PREVIEW_DIR", "./output/previews"))
    )
    ensure_photo_identity_schema(db)
    ensure_push_schema(db)
    settings_store = SettingsStore(
        db,
        master_key=(
            str(
                _config_value(
                    "SETTINGS_MASTER_KEY",
                    os.environ.get("INKTIME_SETTINGS_MASTER_KEY", ""),
                )
                or ""
            )
            if settings_master_key is None
            else settings_master_key
        ),
    )
    provider_client = model_provider or ModelProviderClient()
    analysis_task_service = AnalysisTaskService(db, settings_store)
    auth = WebAuth(
        db,
        initial_password=(
            str(_config_value("ADMIN_INITIAL_PASSWORD", "") or "")
            if initial_admin_password is None
            else initial_admin_password
        ),
        now=auth_now,
    )
    configured_session_secret = session_secret or str(
        _config_value("SESSION_SECRET", "") or ""
    )
    app.secret_key = configured_session_secret or auth.persistent_session_secret()
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(
            bool(_config_value("AUTH_COOKIE_SECURE", False))
            if auth_cookie_secure is None
            else bool(auth_cookie_secure)
        ),
    )
    app.extensions["web_auth"] = auth
    app.extensions["settings_store"] = settings_store
    app.extensions["analysis_task_service"] = analysis_task_service
    should_start_analysis_worker = (
        bool(_config_value("ANALYSIS_WORKER_ENABLED", True)) and db_path is None
        if analysis_worker_enabled is None
        else bool(analysis_worker_enabled)
    )
    if should_start_analysis_worker:
        executor = analysis_executor or LegacyAnalysisExecutor(settings_store)
        analysis_runner = AnalysisWorkerRunner(AnalysisWorker(db, executor))
        analysis_runner.start()
        app.extensions["analysis_worker_runner"] = analysis_runner
        atexit.register(lambda: analysis_runner.shutdown(wait=False))
    asset_maintenance = AssetMaintenance(db, preview_dir)
    app.extensions["asset_maintenance"] = asset_maintenance
    library_root = _resolve_path(scan_root or _config_value("IMAGE_DIR", "./sample_photos"))
    scanner = LibraryScanner(
        db,
        library_root,
        exclude_patterns=list(_config_value("SCAN_EXCLUDE_PATTERNS", []) or []),
    )
    scan_coordinator = ScanCoordinator(scanner)
    app.extensions["scan_coordinator"] = scan_coordinator
    atexit.register(scan_coordinator.shutdown)

    should_scan_startup = (
        bool(_config_value("SCAN_ON_STARTUP", False)) and db_path is None
        if scan_startup is None
        else bool(scan_startup)
    )
    scan_interval = (
        float(_config_value("SCAN_INTERVAL_MINUTES", 0) or 0)
        if scan_interval_minutes is None
        else max(0.0, float(scan_interval_minutes))
    )
    if should_scan_startup:
        scan_coordinator.start("startup")
    if scan_interval > 0 and (db_path is None or scan_interval_minutes is not None):
        scan_scheduler = PeriodicScanScheduler(scan_coordinator, scan_interval)
        scan_scheduler.start()
        app.extensions["scan_scheduler"] = scan_scheduler
        atexit.register(lambda: scan_scheduler.shutdown(wait=False))

    def push_token_error():
        token = str(_config_value("PUSH_API_TOKEN", "") or "")
        if token and request.headers.get("X-Push-Token", "") != token:
            return jsonify({"ok": False, "error": "推送 token 错误或缺失"}), 401
        return None

    def web_status() -> dict[str, Any]:
        status = load_status(
            db,
            monitor_dir=_resolve_path(_config_value("IMAGE_DIR", "./sample_photos")),
            push_dir=push_dir,
        )
        status["monitor_dir"] = _short_path(status.get("monitor_dir"))
        recent_push = status.get("recent_push")
        if isinstance(recent_push, dict):
            recent_push.pop("source_path", None)
            recent_push.pop("render_path", None)
        library_summary = load_library_assets(db, limit=1)["summary"]
        status["tracked_photos"] = library_summary["total"]
        status["monitored_files"] = library_summary["analyzable"]
        status["analyzed_photos"] = library_summary["analysis_status"].get("analyzed", 0)
        status["missing_photos"] = library_summary["file_status"].get("missing", 0)
        status["unreadable_photos"] = library_summary["file_status"].get("unreadable", 0)
        status["unanalyzed_estimate"] = library_summary["analysis_status"].get("pending", 0)
        return status

    public_endpoints = {
        "static",
        "healthz",
        "gallery",
        "api_photos",
        "api_photo_detail",
        "photo_preview",
        "photo_detail",
        "push_static",
        "login_page",
        "login_submit",
        "api_auth_session",
        "api_auth_login",
    }
    password_endpoints = {
        "change_password_page",
        "change_password_submit",
        "api_auth_change_password",
        "api_auth_logout",
        "logout_submit",
        "api_auth_session",
    }

    @app.before_request
    def enforce_auth_boundary():
        if not auth_enabled:
            g.is_admin = True
            g.must_change_password = False
            return None

        state = auth.session_state()
        g.is_admin = bool(state["authenticated"])
        g.must_change_password = bool(state["must_change_password"])
        endpoint = str(request.endpoint or "")
        is_public = endpoint in public_endpoints
        is_api = request.path.startswith("/api/")

        if not g.is_admin and not is_public:
            if is_api:
                return jsonify({"ok": False, "error": "authentication_required"}), 401
            next_url = request.full_path.rstrip("?") or "/"
            return redirect(f"/login?next={quote(next_url, safe='/')}")

        if g.is_admin and g.must_change_password and endpoint not in password_endpoints:
            if is_api:
                return jsonify({"ok": False, "error": "password_change_required"}), 403
            return redirect("/change-password")

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = str(
                request.headers.get("X-CSRF-Token")
                or request.form.get("csrf_token")
                or ""
            )
            expected = auth.csrf_token()
            if not supplied or not secrets.compare_digest(supplied, expected):
                return jsonify({"ok": False, "error": "csrf_failed"}), 400
        return None

    @app.get("/api/auth/session")
    def api_auth_session():
        state = auth.session_state()
        return jsonify(
            {
                "ok": True,
                **state,
                "configured": auth.is_configured(),
                "csrf_token": auth.csrf_token(),
            }
        )

    @app.post("/api/auth/login")
    def api_auth_login():
        payload = request.get_json(silent=True) or {}
        result, status = auth.login(
            str(payload.get("password") or ""), request.remote_addr or "unknown"
        )
        return jsonify(result), status

    @app.get("/login")
    def login_page():
        if g.is_admin and not g.must_change_password:
            return redirect("/")
        next_url = _safe_next_url(request.args.get("next"))
        return _page(
            "管理员登录",
            _render_login_page(
                csrf_token=auth.csrf_token(),
                next_url=next_url,
                configured=auth.is_configured(),
            ),
            active="",
        )

    @app.post("/login")
    def login_submit():
        next_url = _safe_next_url(request.form.get("next"))
        result, status = auth.login(
            str(request.form.get("password") or ""), request.remote_addr or "unknown"
        )
        if status != 200:
            return _page(
                "管理员登录",
                _render_login_page(
                    csrf_token=auth.csrf_token(),
                    next_url=next_url,
                    configured=auth.is_configured(),
                ),
                active="",
            ), status
        if result.get("must_change_password"):
            return redirect("/change-password")
        return redirect(next_url)

    @app.get("/change-password")
    def change_password_page():
        return _page(
            "修改管理员密码",
            _render_change_password_page(csrf_token=auth.csrf_token()),
            active="",
        )

    @app.post("/change-password")
    def change_password_submit():
        result, status = auth.change_password(
            str(request.form.get("current_password") or ""),
            str(request.form.get("new_password") or ""),
        )
        if status != 200:
            return _page(
                "修改管理员密码",
                _render_change_password_page(csrf_token=auth.csrf_token()),
                active="",
            ), status
        return redirect("/")

    @app.post("/api/auth/change-password")
    def api_auth_change_password():
        payload = request.get_json(silent=True) or {}
        result, status = auth.change_password(
            str(payload.get("current_password") or ""),
            str(payload.get("new_password") or ""),
        )
        return jsonify(result), status

    @app.post("/api/auth/logout")
    def api_auth_logout():
        auth.clear_session()
        return jsonify({"ok": True})

    @app.post("/logout")
    def logout_submit():
        auth.clear_session()
        return redirect("/gallery")

    @app.cli.command("reset-admin-password")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def reset_admin_password_command(password: str):
        """Reset the administrator password and revoke existing sessions."""
        try:
            auth.reset_password(password)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo("管理员密码已重置；下次登录必须修改密码。")

    @app.get("/")
    def index():
        status = web_status()
        return _page("InkTime 状态中控台", _render_dashboard_page(status), active="dashboard")

    @app.get("/healthz")
    def healthz():
        return jsonify({"ok": True})

    @app.get("/api/status")
    def api_status():
        return jsonify(web_status())

    def library_query() -> tuple[dict[str, Any], dict[str, str]]:
        values = {key: str(request.args.get(key, "") or "") for key in (
            "file_status", "analysis_status", "visibility_status", "captured_from", "captured_to",
            "has_gps", "file_type", "type", "directory", "filename", "sort", "order",
        )}
        gps = None if values["has_gps"] == "" else values["has_gps"] in {"1", "true", "yes"}
        payload = load_library_assets(
            db,
            file_status=values["file_status"],
            analysis_status=values["analysis_status"],
            visibility_status=values["visibility_status"],
            captured_from=values["captured_from"],
            captured_to=values["captured_to"],
            has_gps=gps,
            file_type=values["file_type"],
            photo_type=values["type"],
            directory=values["directory"],
            filename=values["filename"],
            sort=values["sort"] or "created_at",
            order=values["order"] or "desc",
            limit=min(500, max(1, int(request.args.get("limit", 200)))),
            offset=max(0, int(request.args.get("offset", 0))),
        )
        return payload, values

    @app.get("/library")
    def library():
        payload, filters = library_query()
        return _page("InkTime 素材库", _render_library_page(payload, filters), active="library")

    @app.get("/api/library")
    def api_library():
        payload, _ = library_query()
        return jsonify({"ok": True, **payload})

    @app.post("/api/library/scan")
    def api_library_scan():
        started = scan_coordinator.start("manual")
        response = {"ok": True, "task_id": started.task_id, "reused": started.reused}
        if request.accept_mimetypes.accept_html and not request.is_json:
            return redirect("/library")
        return jsonify(response), 200 if started.reused else 202

    @app.post("/api/library/archive")
    def api_library_archive():
        payload = request.get_json(silent=True) or request.form
        raw_ids = payload.get("photo_ids", [])
        if isinstance(raw_ids, str):
            raw_ids = request.form.getlist("photo_ids") or [raw_ids]
        if not isinstance(raw_ids, (list, tuple)):
            return jsonify({"ok": False, "error": "photo_ids_required"}), 400
        try:
            photo_ids = [int(value) for value in raw_ids]
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "invalid_photo_ids"}), 400
        archived_value = payload.get("archived", True)
        archived = archived_value is True or str(archived_value).lower() in {"1", "true", "yes"}
        updated = asset_maintenance.set_archived(photo_ids, archived=archived)
        if request.accept_mimetypes.accept_html and not request.is_json:
            return redirect("/library?visibility_status=archived" if archived else "/library")
        return jsonify({"ok": True, "updated": updated, "archived": archived})

    @app.get("/api/library/scans/<int:task_id>")
    def api_library_scan_status(task_id: int):
        task = scan_coordinator.task(task_id)
        if task is None:
            abort(404)
        return jsonify({"ok": True, "task": task})

    @app.post("/api/library/selection-preview")
    def api_library_selection_preview():
        try:
            selection = analysis_task_service.preview_selection(request.get_json(silent=True) or {})
        except AnalysisTaskError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
        return jsonify({"ok": True, "selection": selection})

    @app.post("/api/analysis-tasks")
    def api_analysis_tasks_create():
        try:
            task = analysis_task_service.create_task(request.get_json(silent=True) or {})
        except AnalysisTaskError as exc:
            return jsonify({"ok": False, "error": str(exc), "code": exc.code}), exc.status
        return jsonify({"ok": True, "task": task}), 201

    @app.get("/analysis-tasks/<int:task_id>")
    def analysis_task_detail(task_id: int):
        task = analysis_task_service.get_task(task_id)
        if task is None:
            abort(404)
        return _page(
            f"InkTime 分析任务 #{task_id}",
            _render_analysis_task_page(task),
            active="library",
        )

    @app.get("/gallery")
    def gallery():
        sort = str(request.args.get("sort", "score"))
        limit = min(200, max(1, int(request.args.get("limit", 60))))
        today = dt.datetime.now(
            timezone_from_name(str(_config_value("PUSH_TIMEZONE", "Asia/Shanghai")))
        ).date()
        photos = [
            _public_photo(photo)
            for photo in load_photos(
            db,
            limit=limit,
            sort=sort,
            random_seed=request.args.get("seed"),
            exclude_days=int(_config_value("PUSH_EXCLUDE_DAYS", 90)),
            today=today,
            )
        ]
        return _page(
            "InkTime 画廊",
            _render_gallery_page(photos, sort=sort, limit=limit),
            active="gallery",
        )

    @app.get("/api/photos")
    def api_photos():
        sort = str(request.args.get("sort", "score"))
        limit = min(200, max(1, int(request.args.get("limit", 60))))
        include_missing = request.args.get("include_missing") in {"1", "true", "yes"}
        today = dt.datetime.now(
            timezone_from_name(str(_config_value("PUSH_TIMEZONE", "Asia/Shanghai")))
        ).date()
        return jsonify(
            {
                "ok": True,
                "photos": [
                    _photo_api_response(photo)
                    for photo in load_photos(
                        db,
                        limit=limit,
                        sort=sort,
                        include_missing=include_missing,
                        random_seed=request.args.get("seed"),
                        exclude_days=int(_config_value("PUSH_EXCLUDE_DAYS", 90)),
                        today=today,
                    )
                ],
            }
        )

    @app.get("/api/photos/<int:photo_id>")
    def api_photo_detail(photo_id: int):
        photo = load_photo(db, photo_id)
        if photo is None:
            abort(404)
        return jsonify({"ok": True, "photo": _photo_api_response(photo)})

    @app.get("/media/previews/<int:photo_id>.jpg")
    def photo_preview(photo_id: int):
        target = (
            asset_maintenance.ensure_preview(photo_id)
            if g.is_admin
            else asset_maintenance.cached_preview(photo_id)
        )
        if target is None:
            abort(404)
        return send_file(target, mimetype="image/jpeg", max_age=3600)

    @app.get("/api/photos/<int:photo_id>/source")
    def photo_source(photo_id: int):
        target = load_library_source(db, photo_id)
        if target is None:
            abort(404)
        return send_file(target)

    @app.route("/api/photos/<int:photo_id>/overrides", methods=["POST", "PATCH"])
    def save_photo_overrides(photo_id: int):
        photo = load_photo(db, photo_id)
        if photo is None:
            abort(404)
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "请求体必须是 JSON 对象"}), 400

        caption = (
            str(payload.get("custom_side_caption") or "").strip()
            if "custom_side_caption" in payload
            else str(photo.get("custom_side_caption") or "").strip()
        )
        manual_crop = (
            _json_object(payload.get("manual_crop_json"))
            if "manual_crop_json" in payload
            else _json_object(photo.get("manual_crop_json"))
        )
        render_overrides = (
            _json_object(payload.get("render_overrides_json"))
            if "render_overrides_json" in payload
            else _json_object(photo.get("render_overrides_json"))
        )
        render_overrides["display_defaults_version"] = 2
        updated_at = _utc_now()

        conn = sqlite3.connect(db)
        try:
            conn.execute(
                """
                INSERT INTO photo_overrides
                (photo_id, custom_side_caption, manual_crop_json, render_overrides_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(photo_id) DO UPDATE SET
                  custom_side_caption = excluded.custom_side_caption,
                  manual_crop_json = excluded.manual_crop_json,
                  render_overrides_json = excluded.render_overrides_json,
                  updated_at = excluded.updated_at
                """,
                (
                    photo_id,
                    caption or None,
                    json.dumps(manual_crop, ensure_ascii=False, sort_keys=True),
                    json.dumps(render_overrides, ensure_ascii=False, sort_keys=True),
                    updated_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify(
            {
                "ok": True,
                "photo_id": photo_id,
                "custom_side_caption": caption,
                "manual_crop_json": manual_crop,
                "render_overrides_json": render_overrides,
                "updated_at": updated_at,
            }
        )

    @app.post("/api/photos/<int:photo_id>/push")
    def push_photo(photo_id: int):
        auth_error = push_token_error()
        if auth_error is not None:
            return auth_error
        photo = load_photo(db, photo_id)
        if photo is None or not photo.get("exists_on_disk"):
            abort(404)
        item = dict(photo)
        item["source_path"] = str(photo.get("path") or "")
        item["side_caption"] = str(
            photo.get("custom_side_caption") or photo.get("side_caption") or ""
        )
        item["crop_focus"] = _json_object(photo.get("crop_focus_json"))
        settings = settings_from_config(
            db_path=db,
            render_output_dir=render_dir,
            push_output_dir=push_dir,
        )
        try:
            manifest = write_latest_files(
                item,
                settings=settings,
                trigger_type="manual",
                note=f"WebUI photo_id={photo_id}",
            )
        except (FileNotFoundError, OSError, ValueError):
            app.logger.warning("Photo publish failed for id=%s", photo_id)
            return jsonify({"ok": False, "error": "设备成品生成失败"}), 400
        return jsonify(
            {
                "ok": True,
                "photo_id": photo_id,
                "manifest": _safe_manifest_response(manifest),
            }
        )

    @app.get("/photos/<int:photo_id>")
    def photo_detail(photo_id: int):
        photo = load_photo(db, photo_id)
        if photo is None or not photo.get("exists_on_disk"):
            abort(404)
        return _page(
            "照片详情",
            _render_photo_database_detail(_public_photo(photo)),
            active="gallery",
        )

    @app.get("/push-studio/<int:photo_id>")
    def push_studio(photo_id: int):
        photo = load_photo(db, photo_id)
        if photo is None or not photo.get("exists_on_disk"):
            abort(404)
        return _page("单张推送工作台", _render_push_studio_placeholder(photo), active="studio")

    @app.get("/push-studio")
    def push_studio_index():
        photos = load_photos(db, limit=1)
        if photos:
            return redirect(f"/push-studio/{photos[0]['photo_id']}")
        return redirect("/gallery")

    def settings_error(exc: Exception):
        if isinstance(exc, MasterKeyUnavailable):
            return jsonify({"ok": False, "error": "master_key_unavailable"}), 409
        if isinstance(exc, (SettingsError, KeyError)):
            return jsonify({"ok": False, "error": "invalid_settings"}), 400
        raise exc

    @app.get("/api/settings/model-channels")
    def api_settings_channels():
        return jsonify(
            {
                "ok": True,
                "channels": settings_store.list_channels(),
                "presets": settings_store.provider_presets(),
                "capabilities": settings_store.capabilities(),
            }
        )

    @app.post("/api/settings/model-channels")
    def api_settings_channel_create():
        try:
            channel = settings_store.save_channel(request.get_json(silent=True) or {})
        except Exception as exc:
            return settings_error(exc)
        return jsonify({"ok": True, "channel": channel}), 201

    @app.put("/api/settings/model-channels/<channel_id>")
    def api_settings_channel_update(channel_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            channel = settings_store.save_channel({**payload, "id": channel_id})
        except Exception as exc:
            return settings_error(exc)
        return jsonify({"ok": True, "channel": channel})

    @app.delete("/api/settings/model-channels/<channel_id>")
    def api_settings_channel_delete(channel_id: str):
        try:
            result = settings_store.delete_channel(channel_id)
        except Exception as exc:
            return settings_error(exc)
        return jsonify({"ok": True, **result})

    def run_channel_diagnostic(channel_id: str, test: str):
        result, status = _dispatch_channel_diagnostic(
            settings_store,
            provider_client,
            channel_id,
            test,
            request.get_json(silent=True) or {},
        )
        return jsonify(result), status

    @app.post("/api/settings/model-channels/<channel_id>/discover")
    def api_settings_channel_discover(channel_id: str):
        return run_channel_diagnostic(channel_id, "discover")

    @app.post("/api/settings/model-channels/<channel_id>/test-connection")
    def api_settings_channel_test_connection(channel_id: str):
        return run_channel_diagnostic(channel_id, "connection")

    @app.post("/api/settings/model-channels/<channel_id>/test-vision")
    def api_settings_channel_test_vision(channel_id: str):
        return run_channel_diagnostic(channel_id, "vision")

    @app.get("/api/settings/fallback-chain")
    def api_settings_fallback_chain():
        return jsonify({"ok": True, "items": settings_store.get_fallback_chain()})

    @app.put("/api/settings/fallback-chain")
    def api_settings_fallback_chain_save():
        try:
            result = settings_store.save_fallback_chain(
                (request.get_json(silent=True) or {}).get("items") or []
            )
        except Exception as exc:
            return settings_error(exc)
        return jsonify({"ok": True, **result})

    @app.get("/api/settings/<section>")
    def api_settings_section(section: str):
        if section not in {"analysis-defaults", "scan-settings", "security-settings"}:
            abort(404)
        key = section.replace("-", "_")
        result = settings_store.get_section(key)
        return jsonify({"ok": True, **result})

    @app.put("/api/settings/<section>")
    def api_settings_section_save(section: str):
        if section not in {"analysis-defaults", "scan-settings", "security-settings"}:
            abort(404)
        key = section.replace("-", "_")
        try:
            result = settings_store.save_section(key, request.get_json(silent=True) or {})
        except Exception as exc:
            return settings_error(exc)
        return jsonify({"ok": True, **result})

    @app.get("/api/settings/versions/<section>")
    def api_settings_versions(section: str):
        return jsonify({"ok": True, "versions": settings_store.list_versions(section)})

    @app.get("/settings")
    def settings():
        return _page("系统配置", _render_settings_page(), active="settings")

    @app.get("/renders")
    def renders():
        manifest = _load_manifest(render_dir)
        return _page("PhotoPainter 渲染成品", _render_renders_page(manifest), active="gallery")

    @app.get("/renders/<int:item_id>")
    def render_detail(item_id: int):
        manifest = _load_manifest(render_dir)
        renders = manifest.get("renders", [])
        if item_id < 0 or item_id >= len(renders):
            abort(404)
        return _page(
            "渲染效果预览",
            _render_detail_page(manifest, renders[item_id], item_id),
            active="studio",
        )

    @app.get("/review")
    def review():
        rows = _read_review_rows(db)
        return _page("照片分析结果", _render_review_page(rows), active="dashboard")

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
        if filename == "manifest.json":
            try:
                payload = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                abort(404)
            return jsonify(_safe_manifest_response(payload))
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
        auth_error = push_token_error()
        if auth_error is not None:
            return auth_error
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
        except IndexError:
            app.logger.warning("Render item was not found for id=%s", item_id)
            return jsonify({"ok": False, "error": "未找到可推送的渲染记录"}), 404
        except FileNotFoundError:
            app.logger.warning("Render source was not found for id=%s", item_id)
            return jsonify({"ok": False, "error": "推送所需素材不存在"}), 404
        except Exception:
            app.logger.warning("Render publish failed for id=%s", item_id)
            return jsonify({"ok": False, "error": "设备成品生成失败"}), 500
        return jsonify({"ok": True, **_safe_manifest_response(manifest)})

    return app


app = create_app(analysis_worker_enabled=__name__ == "__main__")


if __name__ == "__main__":
    host = str(_config_value("FLASK_HOST", "127.0.0.1"))
    port = int(_config_value("FLASK_PORT", 8765))
    app.run(host=host, port=port, debug=False)
