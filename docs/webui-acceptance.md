# WebUI 第二阶段验收清单

本清单用于本地和飞牛 NAS/Docker 的第二阶段最终验收，同时回归第一阶段画廊和推送合同。验收前应至少完成一次手动或定时推送，确保 `push/latest.bmp`、`latest.png` 和 `manifest.json` 已生成。

## 本地验收

安装依赖并运行静态检查与测试：

```powershell
python -m pip install -r requirements.txt
python -m py_compile analyze_photos.py photopainter_renderer.py render_photopainter.py push_manager.py server.py
python -m unittest discover -v
```

启动服务：

```powershell
python server.py
```

在另一个 PowerShell 窗口运行集中验收：

```powershell
python scripts/validate_webui.py --base-url http://127.0.0.1:8766
```

启用管理员认证时增加 `--expect-auth`，脚本会要求管理路由跳转到同源 `/login`，不会把自动跟随后的登录页误判为业务页面成功：

```powershell
python scripts/validate_webui.py --base-url http://127.0.0.1:8766 --expect-auth
```

脚本会检查：

- `/healthz`
- `/`
- `/gallery`
- `/library`
- `/analysis-tasks`
- `/api/photos?limit=1`
- `/photos/<photo_id>`
- `/push-studio/<photo_id>`
- `/push/latest.png`
- `/push/latest.bmp`
- `/push/manifest.json`
- BMP 为 `800x480`、RGB，并且只包含 PhotoPainter 六色调色板

旧 `/renders`、`/renders/<id>`、`/review` 和 `/render/<id>` 路由继续作为兼容入口保留；新流程使用数据库 `photo_id` 路由。

## NAS/Docker 验收

在 NAS 项目目录构建并启动两个常驻容器：

```bash
sudo docker compose build
sudo docker compose up -d web scheduler
sudo docker compose ps
sudo docker compose logs --tail=100 web scheduler
```

如尚未生成推送产物，执行一次 worker：

```bash
sudo docker compose run --rm worker scheduler.py --run-once --slot 07:00
```

使用同一镜像中的 worker 验证 NAS 服务，避免依赖 NAS 宿主机的 Python 和 Pillow 环境：

```bash
sudo docker compose run --rm worker scripts/validate_webui.py --base-url http://web:8766
```

已配置管理员认证的 NAS 应执行：

```bash
sudo docker compose run --rm worker scripts/validate_webui.py --base-url http://web:8766 --expect-auth
```

也可以在已安装本项目依赖的电脑上运行脚本，并把 `--base-url` 设置为 `http://<NAS-IP>:8766`。

命令返回码为 `0` 且输出 `PASS: WebUI routes and push artifact` 才表示通过。

## 第二阶段功能闭环

集中脚本负责路由、认证边界和当前 BMP 合同；以下有状态流程必须在真实 WebUI/NAS 单独执行并记录结果：

- 设置：保存模型通道、降级链、分析默认值、扫描目录和内外网入口；确认 API 不返回密钥。
- 扫描与选择：重新扫描少量真实照片，确认素材计数、缺失状态和任务成员快照准确。
- 分析任务：完成至少一个真实模型任务并记录实际通道；验证暂停、恢复、失败重试和 Worker 重启后的幂等恢复。
- 分析版本：比较并恢复历史版本；确认长期文案覆盖和推送草稿互不改写。
- 权限：匿名用户只能浏览画廊和照片详情；设置、任务中心与推送工作台必须登录。
- 入口：内外网切换保留当前路径与查询参数，切换 URL 不包含密码、推送 token 或会话 token。
- 推送：手动生成横框 `800x480` 和竖框 `480x800`；定时任务至少生成一种方向。BMP 必须为 RGB 且只含六色调色板，manifest 的 `image_url` 保持 `/push/latest.bmp`。

验收命令、实际结果、降级原因和未通过项应记录到当前集成 PR，并在全部通过后同步到父 PRD。

## 安全检查

提交或部署前确认没有纳入：

- `config.py` 或真实 `.env`
- API key、推送 token
- `photos.db`
- 原始照片目录
- `output/` 生成产物
- 临时日志
