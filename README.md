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
   - `http://127.0.0.1:8765/renders` 查看渲染成品
   - `http://127.0.0.1:8765/review` 查看分析结果

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
RENDER_HEIGHT = 480
RENDER_MODE = "scale"
DITHER_MODE = "atkinson"
BRIGHTNESS = 1.1
CONTRAST = 1.2
SATURATION = 1.2
DAILY_PHOTO_QUANTITY = 5
```

渲染调色板为 PhotoPainter / Spectra 6 六色：黑、白、黄、红、蓝、绿。默认按参考项目使用 `scale` 模式和 Atkinson dithering；也可把 `DITHER_MODE` 改为 `floyd-steinberg`。

注意：这里的 `scale` / `cut` 沿用参考项目语义，`scale` 会填满 800x480 画布并居中裁切，`cut` 会保留完整画面并用白边补齐。

## 运行

先启动你的本地或云端 VLM 服务，然后运行：

```powershell
python analyze_photos.py -j 1 --debug
python render_photopainter.py
python server.py
```

打开：

```text
http://127.0.0.1:8765/renders
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
