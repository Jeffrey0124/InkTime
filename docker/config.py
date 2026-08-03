# -*- coding: utf-8 -*-

"""Docker/NAS 专用配置。

这个文件会在镜像构建时复制为 /app/config.py。所有密钥和 NAS 路径都从
.env 或飞牛 Docker 环境变量读取，不要在这里写死真实 key。
"""

from __future__ import annotations

import os


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _csv(name: str, default: list[str]) -> list[str]:
    raw = _str(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


IMAGE_DIR = "/photos"
DB_PATH = "/data/photos.db"

API_CHANNELS = [
    {
        "name": "local_lmstudio",
        "api_url": _str("LOCAL_VLM_API_URL", "http://192.168.1.50:9100/v1/chat/completions"),
        "api_key": _str("LOCAL_VLM_API_KEY", ""),
        "model_name": _str("LOCAL_VLM_MODEL", "google/gemma-4-31b-qat:2"),
        "timeout": _int("LOCAL_VLM_TIMEOUT", 60),
    },
    {
        "name": "cloud_qwen",
        "api_url": _str(
            "CLOUD_QWEN_API_URL",
            "https://llm-prkpw0ryls2lv0zy.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        "api_key": _str("CLOUD_QWEN_API_KEY", ""),
        "model_name": _str("CLOUD_QWEN_MODEL", "qwen3-vl-plus"),
        "timeout": _int("CLOUD_QWEN_TIMEOUT", 600),
    },
]

BATCH_LIMIT = _int("BATCH_LIMIT", 30)
TIMEOUT = _int("TIMEOUT", 600)
CHANNEL_FAILOVER_COOLDOWN_SEC = _int("CHANNEL_FAILOVER_COOLDOWN_SEC", 300)

FLASK_HOST = "0.0.0.0"
FLASK_PORT = 8766
ENABLE_REVIEW_WEBUI = True

WORLD_CITIES_CSV = "./data/world_cities_zh.csv"
CITY_GRID_DEG = _float("CITY_GRID_DEG", 1.0)
CITY_MAX_DISTANCE_KM = _float("CITY_MAX_DISTANCE_KM", 100.0)

HOME_LAT = _float("HOME_LAT", 22.543096)
HOME_LON = _float("HOME_LON", 114.057865)
HOME_RADIUS_KM = _float("HOME_RADIUS_KM", 60.0)

RENDER_OUTPUT_DIR = "/data/output/photopainter"
RENDER_WIDTH = 800
RENDER_HEIGHT = 432
FINAL_RENDER_HEIGHT = 480
CAPTION_BAR_HEIGHT = 48
RENDER_ORIENTATION = "landscape"
RENDER_MODE = _str("RENDER_MODE", "scale")
DITHER_MODE = _str("DITHER_MODE", "atkinson")
BRIGHTNESS = _float("BRIGHTNESS", 1.1)
CONTRAST = _float("CONTRAST", 1.2)
SATURATION = _float("SATURATION", 1.2)
SAVE_BMP_OUTPUT = _str("SAVE_BMP_OUTPUT", "true").lower() not in {"0", "false", "no"}

FONT_PATH = _str("FONT_PATH", "/usr/local/share/fonts/NotoSansSC-VF.ttf")
DAILY_PHOTO_QUANTITY = _int("DAILY_PHOTO_QUANTITY", 5)

PUSH_OUTPUT_DIR = "/data/output/push"
PUSH_API_TOKEN = _str("INKTIME_PUSH_API_TOKEN", "")
PUSH_SCHEDULES = _csv("PUSH_SCHEDULES", ["07:00", "12:00", "18:40"])
PUSH_EXCLUDE_DAYS = _int("PUSH_EXCLUDE_DAYS", 90)
PUSH_TIMEZONE = _str("PUSH_TIMEZONE", "Asia/Shanghai")
