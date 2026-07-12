# InkTime-PhotoPainter-local Agent Notes

## 项目定位

这个仓库是 `dai-hongtao/InkTime` 的纯软件版 PhotoPainter 复刻，不再面向 ESP32 或任何硬件刷新链路。

核心闭环是：

1. 扫描用户指定照片目录。
2. 调用 OpenAI-compatible 视觉大模型分析照片。
3. 将结果写入 `photos.db` 的 `photo_scores` 表。
4. 按 PhotoPainter / Waveshare 7.3 inch E6 六色墨水屏思路渲染 PNG/BMP。
5. 通过本地 Flask 页面浏览原图、AI 文案、评分理由和六色渲染成品。

默认入口：

```powershell
python analyze_photos.py -j 1 --debug
python render_photopainter.py
python server.py
```

本地页面：

```text
http://127.0.0.1:8766/renders
```

## 绝对边界

不要把硬件链路加回来。这个仓库当前目标是本地软件闭环。

不要恢复或新增：

- `esp32/`
- Arduino / `.ino`
- GxEPD2、WiFi、NVS、AP 配网、Deep Sleep
- `DOWNLOAD_KEY`
- `.bin` 输出、半屏打包、ESP32 静态下载路由
- `/static/inktime/<key>/latest.bin`
- 固件烧录、systemd/crontab 硬件刷新说明

如果需求看起来像“上设备”，先确认用户是否已经改变项目目标。默认仍然只生成本地可浏览成品图。

## 关键文件

- `config.py`：本地私有配置，可能包含 API key，不能泄露到回复或提交里。
- `config-example.py`：公开配置模板，文档和新增字段要同步这里。
- `analyze_photos.py`：图片扫描、VLM 调用、SQLite 入库。
- `photopainter_renderer.py`：PhotoPainter E6 六色渲染核心。
- `render_photopainter.py`：从 `photo_scores` 选图并批量生成成品。
- `server.py`：本地 WebUI，当前首页跳转 `/renders`。
- `output/photopainter/manifest.json`：WebUI 的渲染数据来源。
- `tests/`：渲染、数据流、Web 路由测试。

## 配置经验

用户希望技术文档和说明尽可能使用中文。README、配置说明、运行步骤优先写中文；英文 README 可以保留但不是第一优先级。

大模型使用 OpenAI-compatible 接口。当前应保留双通道：`local_lmstudio` 优先使用本机 LM Studio 根地址，例如 `http://127.0.0.1:9100`，程序会自动补 `/v1/chat/completions`；`model_name` 必须和 `/v1/models` 返回的 id 一致。`cloud_qwen` 作为云端兜底使用 `qwen3-vl-plus`。不要在任何文档、回复、测试或提交中写出真实 API key。

本地私有配置放在 `config.py`，公开模板放在 `config-example.py`。常用字段：

```python
IMAGE_DIR = "./sample_photos"
DB_PATH = "./photos.db"
BATCH_LIMIT = 1
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 8766

API_CHANNELS = [
    {"name": "local_lmstudio", "api_url": "http://127.0.0.1:9100", "api_key": "", "model_name": "google/gemma-4-12b-qat", "timeout": 100},
    {"name": "cloud_qwen", "api_url": "https://你的云端地址/compatible-mode/v1/chat/completions", "api_key": "从环境变量或本地 config.py 读取", "model_name": "qwen3-vl-plus"},
]

RENDER_OUTPUT_DIR = "./output/photopainter"
RENDER_WIDTH = 800
RENDER_HEIGHT = 480
RENDER_ORIENTATION = "landscape"
RENDER_MODE = "scale"
DITHER_MODE = "atkinson"
BRIGHTNESS = 1.1
CONTRAST = 1.2
SATURATION = 1.2
```

小样本优先。调提示词、调评分、调渲染参数时先用 `sample_photos/` 和 `BATCH_LIMIT = 1` 或很小的数量。

## 提示词和分析结果

`analyze_photos.py` 的目标不是只生成一句 caption，而是给后续筛选和展示提供结构化信息：

- `caption`：客观描述画面。
- `side_caption`：适合墨水屏边栏或详情页展示的短文案。
- `type`：照片类型，如旅行、风景、人物、文档等。
- `memory_score`：回忆价值。
- `beauty_score`：视觉美感。
- `reason`：评分理由。
- EXIF / 城市等元数据：用于页面展示和筛选。

改提示词时要保持这些字段稳定，避免破坏 `render_photopainter.py` 和 `server.py` 读取逻辑。

主分析会把实际使用的通道写入 `analysis_channel` 和 `analysis_model`。如果本地模型不可用、超时、HTTP 错误或主分析 JSON 缺字段/分数不可解析，应自动 fallback 到云端，并在日志、manifest 和页面中显示最终通道。

## PhotoPainter E6 渲染要点

渲染算法已经按 Toon-nooT PhotoPainter E-Ink Spectra 6 converter 的核心思路调整：

- 默认目标尺寸：横屏 `800x480`。
- 可选竖屏：`480x800`。
- 六色调色板：黑、白、黄、红、蓝、绿。
- 默认抖动：Atkinson。
- 可选抖动：Floyd-Steinberg。
- 默认模式：`scale`，填满画布并居中裁切。
- 可选模式：`cut`，完整保留图片并留边。
- 输出：浏览器优先看 `PNG`，可保留 `BMP` 作为 PhotoPainter 风格成品。

修改 `photopainter_renderer.py` 后至少验证：

```powershell
python -m unittest tests.test_photopainter_renderer -v
```

关键验收：

- 输出尺寸必须是 `800x480` 或配置尺寸。
- 输出颜色只能属于六色调色板。
- Atkinson 和 Floyd-Steinberg 都能生成图片。
- `scale` 和 `cut` 的语义不能反过来。
- `scale` 模式裁切优先级是 VLM `crop_focus` > OpenCV 人脸检测 > 居中裁切；目标是尽量不裁掉人脸/主体，并在可行时把主体放到黄金分割附近。

## WebUI 经验

`server.py` 是本地审核页面，不是设备下载服务。

保留路由：

- `/`：跳转 `/renders`
- `/renders`：暗色本地渲染画廊
- `/renders/<id>`：单张渲染详情页
- `/review`：AI 分析结果浏览
- `/render/<id>`：单张重新渲染
- `/source/<id>`：查看原图
- `/static/renders/<filename>`：只服务本地渲染图片

不要新增 `.bin` 下载入口。

UI 设计方向：

- 暗色、简洁、偏审核工具。
- 画廊页展示渲染图、短文案、原图路径、类型、回忆度和美观度。
- 详情页左侧展示六色成品图，右侧展示 AI 描述、类型标签、评分条、评分理由和更多信息。
- 页面是工具，不做营销落地页。

服务常用地址是：

```text
http://127.0.0.1:8766/renders
```

如果端口已有旧 Flask 进程，先确认占用进程，再重启。不要留下 `server.log`、`server.err.log` 之类临时日志进仓库。

## 测试和验收

常用测试：

```powershell
python -m py_compile analyze_photos.py photopainter_renderer.py render_photopainter.py server.py
python -m unittest discover -v
python render_photopainter.py
python server.py
```

页面检查：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8766/renders
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8766/renders/0
```

移除硬件链路后，可用这些关键词确认没有运行路径残留：

```powershell
rg "esp32|\\.ino|DOWNLOAD_KEY|latest\\.bin|Deep Sleep|GxEPD2|NVS"
```

注意：文档中解释“已移除的硬件内容”可以出现这些词，但软件运行路径里不应依赖它们。

## Windows 和编码注意事项

这是 Windows PowerShell 环境。优先使用 PowerShell 语法，不要假设 Bash 的 `&&` 一定可用。

中文文件要按 UTF-8 读取和写入。PowerShell 查看中文文件时使用：

```powershell
Get-Content -Raw -Encoding UTF8 README.md
```

终端进度条不要使用复杂 Unicode 块字符。之前在 GBK 控制台里出现过编码错误，命令行输出尽量使用 ASCII，例如 `#` 和 `-`。

## 工作习惯

- 先读本地代码和现有配置，再决定怎么改。
- 用 `rg` 搜索文本和文件。
- 手工改文件使用 `apply_patch`。
- 不要回滚用户或前序任务留下的无关改动。
- 不要在最终回复里暴露 API key。
- 项目说明和交付总结尽量给出可执行命令、具体路径和可验证结果。

## CodeGraph

如果仓库根目录存在 `.codegraph/`，理解代码前先用 CodeGraph；如果不存在，直接使用 `rg` 和常规文件阅读即可。
