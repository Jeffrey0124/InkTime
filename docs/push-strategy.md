# 后端 BMP 推送策略

## 目标

推送流程面向墨水屏设备，而不是浏览器预览页。设备固定下载：

- `/push/latest.bmp`

浏览器调试可打开：

- `/push/latest.png`
- `/push/manifest.json`

## 成品规格

- 最终设备图：`800x480`
- 图像区：`800x432`
- 底部文字条：`800x48`
- 文字字号：`17px`
- 设备格式：24-bit RGB BMP
- 调试预览：PNG

## 后端模块

`push_manager.py` 负责：

- 创建 `push_history` 表。
- 从 `/push-studio/<photo_id>` 调整构图和渲染参数后手动发布成品图。
- 根据日期、评分和推送历史自动选图。
- 写入 `output/push/latest.bmp`、`output/push/latest.png` 和 `manifest.json`。

## API

- `POST /api/photos/<photo_id>/push`
  - 当前 WebUI 推送工作台使用的主接口。
  - 如果配置了 `PUSH_API_TOKEN`，请求头必须包含 `X-Push-Token`。
- `POST /api/push/manual/<render_id>`
  - 旧 manifest 渲染索引的兼容接口。
  - 如果配置了 `PUSH_API_TOKEN`，请求头必须包含 `X-Push-Token`。
- `GET /push/latest.bmp`
  - 墨水屏设备固定下载地址。
- `GET /push/latest.png`
  - 浏览器调试预览。
- `GET /push/manifest.json`
  - 当前发布元数据。

## 定时任务

定时任务不放进 Flask 进程，使用独立入口：

```powershell
python scheduler.py
```

单次测试：

```powershell
python scheduler.py --run-once --slot 07:00
```

未来 Docker 部署建议拆成两个服务：

- `web`: `python server.py`
- `scheduler`: `python scheduler.py`

## 选图规则

自动推送按以下顺序选图：

1. 历史上的今天，EXIF 月日完全一致。
2. 拍摄日期接近今天。
3. 同等条件下优先 `memory_score`，再看 `beauty_score`。
4. 近 90 天推送过的照片默认排除。
5. 如果排除后无候选，则放宽限制，保证有图可推。
