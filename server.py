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


def _page(title: str, body: str, *, active: str = "dashboard") -> str:
    active_dashboard = "active" if active == "dashboard" else ""
    active_gallery = "active" if active == "gallery" else ""
    active_studio = "active" if active == "studio" else ""
    active_settings = "active" if active == "settings" else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
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
        <a class="{active_dashboard}" href="/">
          <span class="nav-icon">⌂</span>
          <span>中控台</span>
        </a>
        <a class="{active_gallery}" href="/gallery">
          <span class="nav-icon">▦</span>
          <span>画廊</span>
        </a>
        <a class="{active_studio}" href="/push-studio">
          <span class="nav-icon">□</span>
          <span>推送工作台</span>
        </a>
        <a class="{active_settings}" href="/settings">
          <span class="nav-icon">◌</span>
          <span>设置</span>
        </a>
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
          <button class="dock-action primary" type="button">开始 / 暂停分析</button>
          <a class="dock-action" href="/api/status">重新扫描照片库</a>
          <button class="dock-action" type="button">停止分析</button>
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
            <button class="text-button" type="button">折叠</button>
          </div>
          <ol class="log-list">
            <li><time>就绪</time><span>本地 WebUI 已启动。</span></li>
            <li><time>数据库</time><span>已分析 {_esc(status.get("analyzed_photos"))} 张照片。</span></li>
            <li><time>目录</time><span>发现 {_esc(status.get("monitored_files"))} 个可用图片文件。</span></li>
            <li><time>缺失</time><span>{_esc(status.get("missing_photos"))} 张照片被标记 missing。</span></li>
          </ol>
        </article>
      </section>
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
        cards.append(
            f"""
            <article class="photo-card{shape}" tabindex="0">
              <a class="push-float" href="/push-studio/{_esc(photo_id)}">加入推送</a>
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
                    <a class="primary-button push-entry" href="/push-studio/{_esc(photo_id)}">进入推送工作台</a>
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
    return f"""
    <section class="screen photo-detail-screen">
      <div class="detail-page-head">
        <span class="status-kicker">Photo #{_esc(photo.get("photo_id"))}</span>
        <div class="actions">
          <a class="button" href="/gallery">返回画廊</a>
          <a class="primary-button" href="/push-studio/{_esc(photo.get("photo_id"))}">进入推送工作台</a>
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
        </aside>
      </section>
    </section>
    """


def _render_push_studio_placeholder(photo: dict[str, Any]) -> str:
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
    <section class="screen studio-screen">
      <div class="studio-head">
        <div>
        <p class="status-kicker">Push Studio</p>
          <h2>单张推送工作台</h2>
          <p class="small">{_esc(meta)}</p>
        </div>
        <a class="button" href="/gallery">返回画廊</a>
      </div>
      <section class="detail-grid studio-workspace">
      <div class="preview-panel">
        <img src="{_esc(photo.get("source_url"))}" alt="{_esc(photo.get("side_caption"))}">
        <div class="paper-caption"><span class="paper-caption-text">{_esc(photo.get("side_caption"))}</span></div>
      </div>
      <aside class="info-panel">
        <h2>推送准备</h2>
        <p class="description">这张照片已经可以作为后续推送工作台的输入。</p>
        <div class="actions">
          <a class="button" href="/gallery#photo-{_esc(photo.get("photo_id"))}">照片详情</a>
        </div>
      </aside>
      </section>
    </section>
    """


def _render_settings_page() -> str:
    return """
    <section class="screen settings-screen" aria-labelledby="settings-title">
      <div class="section-heading">
        <div>
          <p class="kicker">Settings Preview</p>
          <h2 id="settings-title">配置页视觉草案</h2>
        </div>
      </div>
      <div class="settings-layout">
        <article class="setting-row">
          <div>
            <h3>local_lmstudio</h3>
            <p>本地模型通道；真实模型名和 URL 来自本地配置。</p>
          </div>
          <span class="state-label good">可用性待测</span>
        </article>
        <article class="setting-row">
          <div>
            <h3>cloud_qwen</h3>
            <p>云端兜底通道；前端不展示真实 API key。</p>
          </div>
          <span class="state-label">兜底</span>
        </article>
        <article class="setting-row">
          <div>
            <h3>画廊批量偏好</h3>
            <p>批量选择只在选择模式中展开，画廊主视图保持浏览优先。</p>
          </div>
          <label class="setting-control">默认选择前 <input class="mini-input" type="number" value="20"> 张</label>
        </article>
        <article class="setting-note">
          <strong>密钥不在前端显示</strong>
          <p>配置页只展示通道、模型、URL、timeout 和连通性状态；真实 API key 继续来自 config.py 或 NAS .env。</p>
        </article>
      </div>
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
        return _page("InkTime 状态中控台", _render_dashboard_page(status), active="dashboard")

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
        return _page("照片详情", _render_photo_database_detail(photo), active="gallery")

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

    @app.get("/settings")
    def settings():
        return _page("配置页视觉草案", _render_settings_page(), active="settings")

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
