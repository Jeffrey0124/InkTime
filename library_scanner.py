#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""独立照片素材扫描，不创建或消费 AI 分析任务。"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image, ImageOps

from photo_identity import ensure_photo_identity_schema
from photo_fingerprint import content_fingerprint
from notifications import NotificationStore

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - dependency is present in production
    pass


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
DEFAULT_EXCLUDE_PATTERNS = (
    ".git/**",
    "@eaDir/**",
    "#recycle/**",
    "$RECYCLE.BIN/**",
    "System Volume Information/**",
    "**/.git/**",
    "**/@eaDir/**",
    "**/#recycle/**",
    "**/$RECYCLE.BIN/**",
    "**/System Volume Information/**",
    "**/Thumbs.db",
    "**/.DS_Store",
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _is_definitely_missing(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _ratio(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        numerator = getattr(value, "numerator", None)
        denominator = getattr(value, "denominator", None)
        if numerator is None or not denominator:
            raise ValueError("invalid EXIF ratio")
        return float(numerator) / float(denominator)


def _gps_degrees(value: Any) -> float:
    degrees, minutes, seconds = value
    return _ratio(degrees) + _ratio(minutes) / 60 + _ratio(seconds) / 3600


def _extract_metadata(path: Path) -> dict[str, Any]:
    with Image.open(path) as image:
        media_type = str(image.format or path.suffix.lstrip(".")).lower()
        image.load()
        width, height = ImageOps.exif_transpose(image).size
        exif = image.getexif()
        captured_at = str(exif.get(36867) or exif.get(306) or "").strip() or None
        if captured_at:
            try:
                captured_at = dt.datetime.strptime(captured_at, "%Y:%m:%d %H:%M:%S").isoformat()
            except ValueError:
                pass
        gps_lat = gps_lon = gps_alt = None
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo) if exif else {}
        if gps and 2 in gps and 4 in gps:
            gps_lat = _gps_degrees(gps[2])
            gps_lon = _gps_degrees(gps[4])
            if str(gps.get(1, "N")).upper() == "S":
                gps_lat = -gps_lat
            if str(gps.get(3, "E")).upper() == "W":
                gps_lon = -gps_lon
            if 6 in gps:
                gps_alt = _ratio(gps[6])
                if int(gps.get(5, 0) or 0) == 1:
                    gps_alt = -gps_alt
        return {
            "width": width,
            "height": height,
            "media_type": media_type,
            "captured_at": captured_at,
            "gps_lat": gps_lat,
            "gps_lon": gps_lon,
            "gps_alt": gps_alt,
        }


@dataclass(frozen=True)
class ScanResult:
    task_id: int
    discovered_count: int
    readable_count: int
    unreadable_count: int
    missing_count: int


@dataclass(frozen=True)
class ScanStart:
    task_id: int
    reused: bool


class LibraryScanner:
    def __init__(
        self,
        db_path: str | Path,
        root_path: str | Path,
        *,
        exclude_patterns: list[str] | tuple[str, ...] = (),
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.root_path = Path(root_path).expanduser().resolve()
        self.exclude_patterns = tuple(DEFAULT_EXCLUDE_PATTERNS) + tuple(exclude_patterns)
        ensure_photo_identity_schema(self.db_path)

    def _is_excluded(self, relative: Path) -> bool:
        value = relative.as_posix().lstrip("./")
        for pattern in self.exclude_patterns:
            normalized = str(pattern).replace("\\", "/").lstrip("./")
            base = normalized[:-3].rstrip("/") if normalized.endswith("/**") else ""
            if fnmatch.fnmatch(value, normalized) or (base and (value == base or value.startswith(base + "/"))):
                return True
        return False

    def _files(self):
        if not self.root_path.is_dir():
            return
        for directory, dirnames, filenames in os.walk(self.root_path):
            current = Path(directory)
            dirnames[:] = [
                name
                for name in dirnames
                if not self._is_excluded((current / name).relative_to(self.root_path))
            ]
            for name in filenames:
                path = current / name
                relative = path.relative_to(self.root_path)
                if self._is_excluded(relative) or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                yield path, relative

    def _create_task(self, trigger: str) -> int:
        now = _now()
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """
                INSERT INTO scan_tasks
                (status, root_path, trigger_sources_json, created_at, started_at)
                VALUES ('running', ?, ?, ?, ?)
                """,
                (str(self.root_path), json.dumps([trigger]), now, now),
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()

    def scan(self, *, trigger: str, task_id: int | None = None) -> ScanResult:
        active_task_id = task_id or self._create_task(trigger)
        discovered = readable = unreadable = 0
        seen: set[str] = set()
        now = _now()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            tracked = conn.execute("SELECT * FROM photos").fetchall()
            by_path = {str(row["path"]): row for row in tracked}
            assets: list[dict[str, Any]] = []
            for path, relative in self._files() or ():
                discovered += 1
                absolute = str(path.resolve())
                seen.add(absolute)
                stat = None
                fingerprint = None
                reason = None
                try:
                    stat = path.stat()
                    existing = by_path.get(absolute)
                    unchanged = (
                        existing is not None
                        and existing["file_hash"]
                        and int(existing["size_bytes"] or -1) == stat.st_size
                        and float(existing["mtime"] or -1) == stat.st_mtime
                    )
                    fingerprint = (
                        str(existing["file_hash"])
                        if unchanged
                        else content_fingerprint(path)
                    )
                    metadata = _extract_metadata(path)
                    file_status = "present"
                    readable += 1
                except (OSError, ValueError, SyntaxError) as exc:
                    metadata = {
                        "width": None,
                        "height": None,
                        "media_type": path.suffix.lower().lstrip("."),
                        "captured_at": None,
                        "gps_lat": None,
                        "gps_lon": None,
                        "gps_alt": None,
                    }
                    file_status = "unreadable"
                    reason = type(exc).__name__
                    unreadable += 1
                asset = {
                    "path": absolute,
                    "file_hash": fingerprint,
                    "size_bytes": stat.st_size if stat is not None else None,
                    "mtime": stat.st_mtime if stat is not None else None,
                    "file_status": file_status,
                    "filename": path.name,
                    "relative_directory": (
                        relative.parent.as_posix()
                        if relative.parent != Path(".")
                        else ""
                    ),
                    "file_extension": path.suffix.lower(),
                    "media_type": metadata["media_type"],
                    "width": metadata["width"],
                    "height": metadata["height"],
                    "captured_at": metadata["captured_at"],
                    "gps_lat": metadata["gps_lat"],
                    "gps_lon": metadata["gps_lon"],
                    "gps_alt": metadata["gps_alt"],
                    "unreadable_reason": reason,
                    "now": now,
                }
                assets.append(asset)

            new_by_hash: dict[str, list[dict[str, Any]]] = {}
            for asset in assets:
                if asset["path"] not in by_path and asset["file_hash"]:
                    new_by_hash.setdefault(str(asset["file_hash"]), []).append(asset)
            tracked_by_hash: dict[str, list[sqlite3.Row]] = {}
            for row in tracked:
                if row["file_hash"]:
                    tracked_by_hash.setdefault(str(row["file_hash"]), []).append(row)

            for fingerprint, new_assets in new_by_hash.items():
                old_assets = tracked_by_hash.get(fingerprint, [])
                missing_old = []
                for row in old_assets:
                    old_path = Path(str(row["path"]))
                    try:
                        old_path.relative_to(self.root_path)
                    except ValueError:
                        continue
                    if str(old_path) not in seen and _is_definitely_missing(old_path):
                        missing_old.append(row)
                if len(new_assets) != 1 or len(old_assets) != 1 or len(missing_old) != 1:
                    continue
                old = missing_old[0]
                asset = new_assets[0]
                old_path = str(old["path"])
                conn.execute("UPDATE photos SET path=? WHERE id=?", (asset["path"], old["id"]))
                for table, column in (("photo_scores", "path"), ("push_history", "source_path")):
                    exists = conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                    if exists:
                        conn.execute(
                            f"UPDATE {table} SET {column}=? WHERE {column}=?",
                            (asset["path"], old_path),
                        )
                by_path[asset["path"]] = old

            for asset in assets:
                existing = by_path.get(str(asset["path"]))
                analysis_status = str(existing["analysis_status"]) if existing else "pending"
                asset["status"] = "analyzed" if analysis_status == "analyzed" else asset["file_status"]
                if existing:
                    conn.execute(
                        """
                        UPDATE photos SET file_hash=COALESCE(:file_hash, file_hash),
                          size_bytes=COALESCE(:size_bytes, size_bytes),
                          mtime=COALESCE(:mtime, mtime), exists_on_disk=1, status=:status,
                          file_status=:file_status, filename=:filename,
                          relative_directory=:relative_directory,
                          file_extension=:file_extension, media_type=:media_type,
                          width=:width, height=:height, captured_at=:captured_at,
                          gps_lat=:gps_lat, gps_lon=:gps_lon, gps_alt=:gps_alt,
                          unreadable_reason=:unreadable_reason, updated_at=:now,
                          missing_at=NULL, excluded_at=NULL
                        WHERE id=:photo_id
                        """,
                        {**asset, "photo_id": int(existing["id"])},
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO photos
                        (path, file_hash, size_bytes, mtime, exists_on_disk, status,
                         file_status, analysis_status, visibility_status, filename,
                         relative_directory, file_extension, media_type, width, height,
                         captured_at, gps_lat, gps_lon, gps_alt, unreadable_reason,
                         created_at, updated_at)
                        VALUES (:path, :file_hash, :size_bytes, :mtime, 1, :status,
                                :file_status, 'pending', 'active', :filename,
                                :relative_directory, :file_extension, :media_type,
                                :width, :height, :captured_at, :gps_lat, :gps_lon,
                                :gps_alt, :unreadable_reason, :now, :now)
                        """,
                        asset,
                    )

            missing = 0
            prefix = str(self.root_path) + os.sep
            tracked = conn.execute(
                "SELECT id, path FROM photos WHERE path LIKE ?", (prefix + "%",)
            ).fetchall()
            for photo_id, raw_path in tracked:
                if raw_path in seen:
                    continue
                path = Path(raw_path)
                if path.is_file():
                    relative = path.relative_to(self.root_path)
                    if self._is_excluded(relative):
                        conn.execute(
                            """
                            UPDATE photos SET exists_on_disk=1, status='excluded',
                              file_status='excluded', updated_at=?, excluded_at=COALESCE(excluded_at, ?),
                              missing_at=NULL WHERE id=?
                            """,
                            (now, now, photo_id),
                        )
                    continue
                missing += 1
                conn.execute(
                    """
                    UPDATE photos SET exists_on_disk=0, status='missing',
                      file_status='missing', updated_at=?,
                      missing_at=COALESCE(missing_at, ?)
                    WHERE id=?
                    """,
                    (now, now, photo_id),
                )
            conn.execute(
                """
                UPDATE scan_tasks SET status='completed', discovered_count=?,
                  readable_count=?, unreadable_count=?, missing_count=?, finished_at=?
                WHERE id=?
                """,
                (discovered, readable, unreadable, missing, now, active_task_id),
            )
            conn.commit()
            return ScanResult(active_task_id, discovered, readable, unreadable, missing)
        except Exception as exc:
            conn.rollback()
            conn.execute(
                "UPDATE scan_tasks SET status='failed', finished_at=?, error_message=? WHERE id=?",
                (_now(), type(exc).__name__, active_task_id),
            )
            conn.commit()
            NotificationStore(self.db_path).create_once(
                kind="scan_failed",
                title="素材扫描失败",
                message="素材扫描未完成，请检查目录可访问性后重试。",
                target_url=f"/library/scans/{active_task_id}",
            )
            raise
        finally:
            conn.close()


class ScanCoordinator:
    def __init__(self, scanner: LibraryScanner) -> None:
        self.scanner = scanner
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="library-scan")
        self._lock = threading.Lock()
        self._futures: dict[int, Future[ScanResult]] = {}

    def _active_task(self, trigger: str) -> int | None:
        conn = sqlite3.connect(self.scanner.db_path)
        try:
            row = conn.execute(
                "SELECT id, trigger_sources_json FROM scan_tasks WHERE status IN ('queued', 'running') ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            task_id = int(row[0])
            future = self._futures.get(task_id)
            if future is None or future.done():
                conn.execute(
                    """
                    UPDATE scan_tasks
                    SET status='failed', finished_at=?,
                        error_message='interrupted_by_process_restart'
                    WHERE id=? AND status IN ('queued', 'running')
                    """,
                    (_now(), task_id),
                )
                conn.commit()
                return None
            sources = json.loads(row[1] or "[]")
            if trigger not in sources:
                sources.append(trigger)
                conn.execute(
                    "UPDATE scan_tasks SET trigger_sources_json=? WHERE id=?",
                    (json.dumps(sources), task_id),
                )
                conn.commit()
            return task_id
        finally:
            conn.close()

    def start(self, trigger: str) -> ScanStart:
        with self._lock:
            active = self._active_task(trigger)
            if active is not None:
                return ScanStart(active, True)
            try:
                task_id = self.scanner._create_task(trigger)
            except sqlite3.IntegrityError:
                active = self._active_task(trigger)
                if active is None:
                    raise
                return ScanStart(active, True)
            self._futures[task_id] = self._executor.submit(
                self.scanner.scan, trigger=trigger, task_id=task_id
            )
            return ScanStart(task_id, False)

    def wait(self, task_id: int, timeout: float | None = None) -> ScanResult:
        return self._futures[task_id].result(timeout=timeout)

    def task(self, task_id: int) -> dict[str, Any] | None:
        conn = sqlite3.connect(self.scanner.db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT * FROM scan_tasks WHERE id=?", (task_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        result = dict(row)
        result["trigger_sources"] = json.loads(result.pop("trigger_sources_json") or "[]")
        result.pop("root_path", None)
        return result

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)


class PeriodicScanScheduler:
    """Small daemon scheduler that always delegates to the shared coordinator."""

    def __init__(self, coordinator: ScanCoordinator, interval_minutes: float) -> None:
        self.coordinator = coordinator
        self.interval_seconds = max(0.01, float(interval_minutes) * 60)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="library-scan-scheduler",
            daemon=True,
        )
        self.running = False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.coordinator.start("scheduled")

    def shutdown(self, wait: bool = False) -> None:
        if not self.running:
            return
        self.running = False
        self._stop.set()
        if wait and self._thread.is_alive():
            self._thread.join(timeout=5)
