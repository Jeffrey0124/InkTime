#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Shared compatibility schema for the legacy photo_scores projection."""

from __future__ import annotations

import sqlite3


PHOTO_SCORE_COLUMNS = {
    "caption": "TEXT",
    "type": "TEXT",
    "memory_score": "REAL",
    "beauty_score": "REAL",
    "reason": "TEXT",
    "width": "INTEGER",
    "height": "INTEGER",
    "orientation": "TEXT",
    "used_at": "TEXT",
    "exif_json": "TEXT",
    "raw_json": "TEXT",
    "exif_datetime": "TEXT",
    "exif_make": "TEXT",
    "exif_model": "TEXT",
    "exif_iso": "INTEGER",
    "exif_exposure_time": "REAL",
    "exif_f_number": "REAL",
    "exif_focal_length": "REAL",
    "exif_gps_lat": "REAL",
    "exif_gps_lon": "REAL",
    "exif_gps_alt": "REAL",
    "side_caption": "TEXT",
    "exif_city": "TEXT",
    "location_hint": "TEXT",
    "analysis_channel": "TEXT",
    "analysis_model": "TEXT",
    "crop_focus_json": "TEXT",
}


def ensure_photo_scores_schema(conn: sqlite3.Connection) -> None:
    columns = ",\n          ".join(
        f"{name} {definition}" for name, definition in PHOTO_SCORE_COLUMNS.items()
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS photo_scores (
          path TEXT PRIMARY KEY,
          {columns}
        )
        """
    )
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(photo_scores)")}
    for name, definition in PHOTO_SCORE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE photo_scores ADD COLUMN {name} {definition}")

