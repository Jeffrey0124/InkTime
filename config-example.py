# -*- coding: utf-8 -*-

# 小样本相册目录。建议先放 10-30 张测试图片，确认流程和提示词效果后再扩大范围。
IMAGE_DIR = "./sample_photos"

# analyze_photos.py 生成的 SQLite 数据库。
DB_PATH = "./photos.db"

# OpenAI-compatible 视觉模型渠道，按优先级从高到低排列。
API_CHANNELS = [
    {
        "api_url": "http://127.0.0.1:1234/v1/chat/completions",
        "api_key": "",
        "model_name": "qwen3-vl-32b-instruct",
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
RENDER_HEIGHT = 480
RENDER_ORIENTATION = "landscape"
RENDER_MODE = "scale"
DITHER_MODE = "atkinson"
BRIGHTNESS = 1.1
CONTRAST = 1.2
SATURATION = 1.2
SAVE_BMP_OUTPUT = True

# 可选字体路径。当前默认渲染不叠字，保留给后续带文字版布局使用。
FONT_PATH = ""

# 每次渲染到本地画廊的照片数量。
DAILY_PHOTO_QUANTITY = 5
