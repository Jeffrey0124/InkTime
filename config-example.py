# -*- coding: utf-8 -*-

import os

# 小样本相册目录。建议先放 10-30 张测试图片，确认流程和提示词效果后再扩大范围。
IMAGE_DIR = "./sample_photos"

# analyze_photos.py 生成的 SQLite 数据库。
DB_PATH = "./photos.db"

# OpenAI-compatible 视觉模型渠道，按优先级从高到低排列。
# 运行逻辑：永远先请求 local_lmstudio；本地不可用、超时、HTTP 错误或输出 JSON 不合格时，
# 自动切换到 cloud_qwen。真实云端 key 请放在本地 config.py 或环境变量里，不要提交。
API_CHANNELS = [
    {
        "name": "local_lmstudio",
        # 可以填 LM Studio 根地址；程序会自动补 /v1/chat/completions。
        "api_url": "http://127.0.0.1:9100",
        "api_key": "",
        "model_name": "google/gemma-4-31b-qat:2",
        "timeout": 60,
    },
    {
        "name": "cloud_qwen",
        "api_url": "https://你的云端地址/compatible-mode/v1/chat/completions",
        "api_key": os.environ.get("INKTIME_CLOUD_API_KEY", ""),
        "model_name": "qwen3-vl-plus",
        "timeout": 600,
    },
]

# 调试阶段建议限制处理数量，避免一次性消耗太多模型调用。
BATCH_LIMIT = 30

# 模型请求超时时间，单位：秒。
TIMEOUT = 600

# 某个渠道失败后，临时降低优先级的冷却时间，单位：秒。
CHANNEL_FAILOVER_COOLDOWN_SEC = 300

# 本地 Flask 预览服务。
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 8765
ENABLE_REVIEW_WEBUI = True

# WebUI 单管理员认证。首次密码和稳定会话密钥只从环境变量读取。
ADMIN_INITIAL_PASSWORD = os.environ.get("INKTIME_ADMIN_INITIAL_PASSWORD", "")
SESSION_SECRET = os.environ.get("INKTIME_SESSION_SECRET", "")
AUTH_COOKIE_SECURE = os.environ.get("INKTIME_AUTH_COOKIE_SECURE", "false").lower() in {
    "1",
    "true",
    "yes",
}
# 设置页数据库凭据的加密主密钥。留空时禁止向数据库保存 API Key。
SETTINGS_MASTER_KEY = os.environ.get("INKTIME_SETTINGS_MASTER_KEY", "")
DISPLAY_PREVIEW_DIR = "./output/previews"

# 素材库扫描：启动时扫描一次，并按间隔复扫。自定义排除规则相对于 IMAGE_DIR。
SCAN_ON_STARTUP = True
SCAN_INTERVAL_MINUTES = 30
SCAN_EXCLUDE_PATTERNS = ["private/**"]

# 离线中文城市名索引，用于根据照片 GPS 信息补充城市名。
WORLD_CITIES_CSV = "./data/world_cities_zh.csv"
CITY_GRID_DEG = 1.0
CITY_MAX_DISTANCE_KM = 100.0

# 常驻地坐标。analyze_photos.py 会用它给旅行照片做小幅回忆分加成。
HOME_LAT = 22.543096
HOME_LON = 114.057865
HOME_RADIUS_KM = 60.0

# PhotoPainter Spectra 6 本地渲染输出。
RENDER_OUTPUT_DIR = "./output/photopainter"
RENDER_WIDTH = 800
RENDER_HEIGHT = 432
FINAL_RENDER_HEIGHT = 480
CAPTION_BAR_HEIGHT = 48
RENDER_ORIENTATION = "landscape"
RENDER_MODE = "scale"
# PhotoPainter 原版前向 Atkinson，适合照片和人像，作为默认推荐算法。
DITHER_MODE = "atkinson"
BRIGHTNESS = 1.1
CONTRAST = 1.2
SATURATION = 1.2
SAVE_BMP_OUTPUT = True

# 可选字体路径。当前默认渲染不叠字，保留给后续带文字版布局使用。
FONT_PATH = ""

# 每次渲染到本地画廊的照片数量。
DAILY_PHOTO_QUANTITY = 5

# 墨水屏自动推送。设备固定读取 /push/latest.bmp；PNG 仅用于浏览器调试。
PUSH_OUTPUT_DIR = "./output/push"
PUSH_API_TOKEN = os.environ.get("INKTIME_PUSH_API_TOKEN", "")
PUSH_SCHEDULES = ["07:00", "12:00", "18:40"]
PUSH_EXCLUDE_DAYS = 90
PUSH_TIMEZONE = "Asia/Shanghai"
