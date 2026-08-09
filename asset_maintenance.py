#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""素材归档与不暴露原文件路径的彩色预览缓存。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps

from photo_fingerprint import content_fingerprint


class AssetMaintenance:
    def __init__(self, db_path: str | Path, preview_dir: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.preview_dir = Path(preview_dir).expanduser().resolve()

    def asset_state(self, photo_id: int) -> dict[str, object] | None:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT id, path, file_hash, exists_on_disk, file_status, visibility_status "
                "FROM photos WHERE id=?",
                (photo_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def set_archived(self, photo_ids: Iterable[int], *, archived: bool) -> int:
        ids = sorted({int(value) for value in photo_ids if int(value) > 0})
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        visibility = "archived" if archived else "active"
        archived_at = "CURRENT_TIMESTAMP" if archived else "NULL"
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                f"UPDATE photos SET visibility_status=?, archived_at={archived_at}, "
                f"updated_at=CURRENT_TIMESTAMP WHERE id IN ({placeholders})",
                [visibility, *ids],
            )
            conn.commit()
            return int(cursor.rowcount)
        finally:
            conn.close()

    def _cache_target(self, photo_id: int, fingerprint: str) -> Path:
        return self.preview_dir / f"{photo_id}-{fingerprint}.jpg"

    def cached_preview(self, photo_id: int) -> Path | None:
        state = self.asset_state(photo_id)
        if (
            state is None
            or not state.get("file_hash")
            or state.get("visibility_status") != "active"
        ):
            return None
        target = self._cache_target(photo_id, str(state["file_hash"]))
        return target if target.is_file() else None

    def ensure_preview(self, photo_id: int) -> Path | None:
        state = self.asset_state(photo_id)
        if state is None or not state.get("exists_on_disk"):
            return None
        source = Path(str(state["path"])).expanduser()
        if not source.is_file():
            return None
        try:
            fingerprint = content_fingerprint(source)
            stat = source.stat()
        except OSError:
            return None
        if fingerprint != state.get("file_hash"):
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    "UPDATE photos SET file_hash=?, size_bytes=?, mtime=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (fingerprint, stat.st_size, stat.st_mtime, photo_id),
                )
                conn.commit()
            finally:
                conn.close()
        target = self._cache_target(photo_id, fingerprint)
        if not target.is_file():
            self.preview_dir.mkdir(parents=True, exist_ok=True)
            try:
                with Image.open(source) as image:
                    display = ImageOps.exif_transpose(image).convert("RGB")
                    display.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                    display.save(target, format="JPEG", quality=86, optimize=True)
            except (OSError, ValueError, SyntaxError):
                target.unlink(missing_ok=True)
                return None
        for stale in self.preview_dir.glob(f"{photo_id}-*.jpg"):
            if stale != target:
                stale.unlink(missing_ok=True)
        return target
