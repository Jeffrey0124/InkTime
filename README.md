# InkTime PhotoPainter Local

本仓库是 InkTime 的纯软件复刻版：用少量测试照片跑通“AI 分析图片 -> SQLite 入库 -> PhotoPainter 7.3 寸 Spectra 6 六色渲染 -> 本地浏览器查看成品图”的闭环。

它不包含任何硬件、烧录、下载或刷屏逻辑。输出目标是浏览器可查看的 `PNG`，可选同时保存 `BMP`。

## 工作流

1. `analyze_photos.py`
   扫描本地相册，调用 OpenAI-compatible 视觉模型，生成描述、评分和一句话文案，写入 `photos.db`。

2. `render_photopainter.py`
   从 `photo_scores` 读取高分照片，按 PhotoPainter 7.3 寸 Spectra 6 屏幕风格渲染为六色图片。

3. `server.py`
   提供数据库驱动的本地 WebUI：
   - `http://127.0.0.1:8766/` 查看状态中控台
   - `http://127.0.0.1:8766/gallery` 浏览已分析照片
   - `/photos/<photo_id>` 查看照片详情并微调文案
   - `/push-studio/<photo_id>` 调整构图和六色参数并手动推送

## 环境准备

推荐 Python 3.10+。

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config-example.py config.py
```

把 10-30 张测试图片放进 `sample_photos/`，然后按你的 VLM 服务修改 `config.py` 里的 `API_CHANNELS`。

## 配置重点

`config.py` 默认只面向本地软件流程：

```python
IMAGE_DIR = "./sample_photos"
DB_PATH = "./photos.db"
RENDER_OUTPUT_DIR = "./output/photopainter"
RENDER_WIDTH = 800
RENDER_HEIGHT = 432
FINAL_RENDER_HEIGHT = 480
CAPTION_BAR_HEIGHT = 48
RENDER_MODE = "scale"
DITHER_MODE = "atkinson"
BRIGHTNESS = 1.1
CONTRAST = 1.2
SATURATION = 1.2
DAILY_PHOTO_QUANTITY = 5
```

### 双通道视觉模型

`API_CHANNELS` 按顺序尝试。当前推荐保留两路：

```python
API_CHANNELS = [
    {
        "name": "local_lmstudio",
        "api_url": "http://127.0.0.1:9100",
        "api_key": "",
        "model_name": "google/gemma-4-31b-qat:2",
        "timeout": 60,
    },
    {
        "name": "cloud_qwen",
        "api_url": "https://你的云端地址/compatible-mode/v1/chat/completions",
        "api_key": "从环境变量或本地 config.py 读取",
        "model_name": "qwen3-vl-plus",
        "timeout": 600,
    },
]
```

运行逻辑：

- 每张图先请求 `local_lmstudio`。
- `api_url` 可以填 LM Studio 的根地址，例如 `http://127.0.0.1:9100`，程序会自动补成 `/v1/chat/completions`。
- `model_name` 必须和 LM Studio 的 `/v1/models` 返回的 `id` 一致；如果只加载了 `google/gemma-4-12b-qat`，就不要填 `google/gemma-4-31b-qat`。
- 本地模型不可用、超过该通道 `timeout`、HTTP 错误或主分析 JSON 缺字段/分数不可解析时，自动请求 `cloud_qwen`。
- 数据库会记录 `analysis_channel` 和 `analysis_model`。
- 画廊、照片详情和推送工作台会展示照片实际使用的分析通道或模型。

渲染调色板为 PhotoPainter / Spectra 6 六色：黑、白、黄、红、蓝、绿。默认按参考项目使用 `scale` 模式和 Atkinson dithering；也可把 `DITHER_MODE` 改为 `floyd-steinberg`。

注意：最终成品包含底部文字条。默认横框为 800x480（图像区 800x432、文字条 800x48）；推送工作台切换竖框后为 480x800（图像区 480x704、文字条 480x96）。照片旋转只改变图像内容，不改变相框方向。这里的 `scale` / `cut` 沿用参考项目语义，`scale` 会填满图像区并按主体裁切，`cut` 会保留完整画面并用白边补齐。

### 智能裁切

`scale` 模式现在不是简单居中裁切，而是按以下优先级决定裁切窗口：

1. `analyze_photos.py` 在识别照片时要求视觉模型输出 `crop_focus`，即主体/人脸/宠物脸/关键内容的相对坐标。
2. `render_photopainter.py` 读取数据库中的 `crop_focus_json`，渲染时尽量让该区域完整留在 800x432 图像区内。
3. 如果没有模型裁切范围，则尝试用 OpenCV 检测人脸并保住人脸。
4. 如果以上都没有结果，才退回居中裁切。

裁切算法会在不裁掉主体的前提下，尽量把主体中心放到黄金分割附近；多人合影会优先保住所有关键人脸。

## 运行

先启动你的本地或云端 VLM 服务，然后运行：

```powershell
python analyze_photos.py -j 1 --debug
python render_photopainter.py
python server.py
```

打开：

```text
http://127.0.0.1:8766/
http://127.0.0.1:8766/gallery
```

旧 `/renders`、`/renders/<id>`、`/review` 和 `/render/<id>` 路由暂时保留，供已有书签或脚本兼容；新操作流程应使用数据库 `photo_id` 路由。

渲染产物位于：

```text
output/photopainter/
```

主要文件：

- `render_000.png`, `render_001.png`, ...
- `latest.png`
- `manifest.json`
- 可选 `render_000.bmp`, `latest.bmp`

## 自动推送

推送给墨水屏设备的成品图固定为 BMP：

- 设备下载：`http://127.0.0.1:8766/push/latest.bmp`
- 浏览器预览：`http://127.0.0.1:8766/push/latest.png`
- 发布状态：`http://127.0.0.1:8766/push/manifest.json`

在推送工作台点击“生成并推送”会按相框方向生成完整横框 `800x480` 或竖框 `480x800` 成品图。整个成品（包括底部文字条）统一经过六色抖动。如果设置了 `PUSH_API_TOKEN`，手动推送需要输入 token；设备下载 BMP 不需要鉴权。

自动推送使用独立进程，便于未来 Docker 拆分为 `web` 和 `scheduler`：

```powershell
python scheduler.py
python scheduler.py --run-once --slot 07:00
```

详细设计见 `docs/push-strategy.md`。

## WebUI 管理员登录

WebUI 默认启用单管理员认证。首次启动前，在本地 `config.py` 或 NAS `.env` 中配置至少 8 个字符的一次性初始密码，以及至少 32 个随机字符的稳定会话密钥；首次登录后必须修改密码。访客无需登录即可查看画廊、照片详情和缓存预览，管理页面、原图与写操作需要管理员会话。

忘记密码时，在服务端执行重置命令；该操作会注销全部已有会话，并要求下次登录再次修改密码：

```powershell
$env:FLASK_APP = "server.py"
python -m flask reset-admin-password
```

不要把初始密码、会话密钥或重置后的密码提交到 Git。

## 素材库扫描

WebUI 启动扫描、周期扫描和“重新扫描”按钮共用一个活动任务。扫描只维护素材、文件状态和 EXIF 元数据，不创建 AI 任务，也不会删除历史分析。

```env
SCAN_ON_STARTUP=true
SCAN_INTERVAL_MINUTES=30
SCAN_EXCLUDE_PATTERNS=private/**,exports/**
```

管理员登录后访问 `/library`，可以按文件/分析状态、拍摄日期、GPS、类型、目录和文件名筛选，并按拍摄日期、入库时间、文件名或文件大小双向排序。

本地开发时另开一个终端运行 `python analysis_worker.py`，它会在全局分析队列空闲时自动领取 WebUI 创建的任务。需要只消费一个任务进行调试时使用 `python analysis_worker.py --once`。

## 飞牛 NAS Docker 部署

推荐使用 Docker Compose 的三个常驻容器形态：

- `web`：运行 `python server.py`，提供 WebUI 和 `/push/latest.bmp`。
- `scheduler`：运行 `python scheduler.py`，负责定时自动推送。
- `analysis-worker`：运行 `python analysis_worker.py`，自动消费 WebUI 创建的分析任务。
- `worker`：不常驻，只用于按需执行旧分析、渲染和单次推送测试。

快速命令：

```bash
cp .env.example .env
docker compose build
docker compose up -d web scheduler analysis-worker
docker compose run --rm worker analyze_photos.py -j 1 --debug
docker compose run --rm worker render_photopainter.py
docker compose run --rm worker scheduler.py --run-once --slot 07:00
```

NAS 容器内不能用 `127.0.0.1` 访问你电脑上的 LM Studio。本地模型地址请在 `.env` 里改成电脑的局域网固定 IP，例如 `http://192.168.1.50:9100/v1/chat/completions`。

完整部署说明见 `docs/docker-deployment.md`。

## 测试

```powershell
python -m py_compile analyze_photos.py analysis_worker.py photopainter_renderer.py render_photopainter.py push_manager.py server.py
python -m unittest discover -v
python -c "import flask, PIL, pillow_heif, numpy; print('ok')"
python scripts/validate_webui.py --base-url http://127.0.0.1:8766
```

最后一条命令要求服务已启动且已经生成 `push/latest.bmp`，它会检查健康接口、新 WebUI 路由、push manifest，以及 BMP 的 `800x480`、RGB 和 PhotoPainter 六色约束。NAS 验收时把 `--base-url` 改为 NAS 地址即可。

完整的本地与 NAS/Docker 验收步骤见 `docs/webui-acceptance.md`。

## 说明

本项目参考 PhotoPainter Spectra 6 转换思路：先做尺寸适配和图像增强，再限制到六色调色板，并通过抖动改善观感。当前版本专注本地预览和调参，不生成设备刷写文件。
