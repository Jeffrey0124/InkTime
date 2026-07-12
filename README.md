# InkTime PhotoPainter Local

本仓库是 InkTime 的纯软件复刻版：用少量测试照片跑通“AI 分析图片 -> SQLite 入库 -> PhotoPainter 7.3 寸 Spectra 6 六色渲染 -> 本地浏览器查看成品图”的闭环。

它不包含任何硬件、烧录、下载或刷屏逻辑。输出目标是浏览器可查看的 `PNG`，可选同时保存 `BMP`。

## 工作流

1. `analyze_photos.py`
   扫描本地相册，调用 OpenAI-compatible 视觉模型，生成描述、评分和一句话文案，写入 `photos.db`。

2. `render_photopainter.py`
   从 `photo_scores` 读取高分照片，按 PhotoPainter 7.3 寸 Spectra 6 屏幕风格渲染为六色图片。

3. `server.py`
   提供本地 WebUI：
   - `http://127.0.0.1:8766/renders` 查看渲染成品
   - `http://127.0.0.1:8766/review` 查看分析结果

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
- `/renders`、`/renders/<id>` 和 `/review` 会展示这张图实际由哪个通道分析。

渲染调色板为 PhotoPainter / Spectra 6 六色：黑、白、黄、红、蓝、绿。默认按参考项目使用 `scale` 模式和 Atkinson dithering；也可把 `DITHER_MODE` 改为 `floyd-steinberg`。

注意：最终预览成品固定为 800x480，其中上方图像区为 800x432，底部文字条为 800x48。这里的 `scale` / `cut` 沿用参考项目语义，`scale` 会填满 800x432 图像区并按主体裁切，`cut` 会保留完整画面并用白边补齐。

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
http://127.0.0.1:8766/renders
```

渲染产物位于：

```text
output/photopainter/
```

主要文件：

- `render_000.png`, `render_001.png`, ...
- `latest.png`
- `manifest.json`
- 可选 `render_000.bmp`, `latest.bmp`

## 测试

```powershell
python -m unittest discover -v
python -c "import flask, PIL, pillow_heif, numpy; print('ok')"
```

## 说明

本项目参考 PhotoPainter Spectra 6 转换思路：先做尺寸适配和图像增强，再限制到六色调色板，并通过抖动改善观感。当前版本专注本地预览和调参，不生成设备刷写文件。
