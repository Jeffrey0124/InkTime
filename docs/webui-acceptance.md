# WebUI 第一阶段验收清单

本清单用于本地和飞牛 NAS/Docker 的第一阶段最终验收。验收前应至少完成一次手动或定时推送，确保 `push/latest.bmp`、`latest.png` 和 `manifest.json` 已生成。

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

脚本会检查：

- `/healthz`
- `/`
- `/gallery`
- `/api/photos?limit=1`
- `/photos/<photo_id>`
- `/push-studio/<photo_id>`
- `/push/latest.png`
- `/push/latest.bmp`
- `/push/manifest.json`
- BMP 为 `800x480`、RGB，并且只包含 PhotoPainter 六色调色板

旧 `/renders`、`/renders/<id>`、`/review` 和 `/render/<id>` 路由继续作为兼容入口保留；新流程使用数据库 `photo_id` 路由。

## NAS/Docker 验收

在 NAS 项目目录构建并启动三个常驻容器：

```bash
sudo docker compose build
sudo docker compose up -d web scheduler analysis-worker
sudo docker compose ps
sudo docker compose logs --tail=100 web scheduler analysis-worker
```

如尚未生成推送产物，执行一次 worker：

```bash
sudo docker compose run --rm worker scheduler.py --run-once --slot 07:00
```

使用同一镜像中的 worker 验证 NAS 服务，避免依赖 NAS 宿主机的 Python 和 Pillow 环境：

```bash
sudo docker compose run --rm worker scripts/validate_webui.py --base-url http://web:8766
```

也可以在已安装本项目依赖的电脑上运行脚本，并把 `--base-url` 设置为 `http://<NAS-IP>:8766`。

命令返回码为 `0` 且输出 `PASS: WebUI routes and push artifact` 才表示通过。

## 安全检查

提交或部署前确认没有纳入：

- `config.py` 或真实 `.env`
- API key、推送 token
- `photos.db`
- 原始照片目录
- `output/` 生成产物
- 临时日志
