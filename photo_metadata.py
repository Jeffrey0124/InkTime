#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""从原始照片补读可供 WebUI 和推送流程使用的位置元数据。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def coordinate_label(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return ""
    return f"{float(lat):.4f}, {float(lon):.4f}"


def read_location_from_source(source: str | Path) -> dict[str, Any]:
    path = Path(source).expanduser()
    if not path.exists() or not path.is_file():
        return {}

    try:
        from analyze_photos import get_city_resolver, read_exif

        info = read_exif(path)
        lat = info.get("gps_lat")
        lon = info.get("gps_lon")
        if lat is None or lon is None:
            return {}
        lat = float(lat)
        lon = float(lon)
        try:
            city = get_city_resolver()(lat, lon)
        except (Exception, SystemExit):
            city = ""
        return {
            "lat": lat,
            "lon": lon,
            "alt": info.get("gps_alt"),
            "city": city,
            "display": city or coordinate_label(lat, lon),
        }
    except (Exception, SystemExit):
        return {}
