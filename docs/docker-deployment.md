# 飞牛 NAS Docker 部署

本项目推荐使用一个镜像、三个常驻容器：

- `inktime-web`：运行 `python server.py`，提供 WebUI 和 `/push/latest.bmp`。
- `inktime-scheduler`：运行 `python scheduler.py`，按时间自动发布 BMP。
- `inktime-analysis-worker`：运行 `python analysis_worker.py`，自动消费分析任务队列。

旧分析脚本、批量渲染和运维测试仍使用 `docker compose run --rm worker ...` 按需执行。

## 目录规划

在飞牛 NAS 上准备两个目录：

```text
/vol1/你的照片目录                 # 只读照片源
/vol1/docker/inktime/data          # 数据库和输出目录
```

容器内固定路径：

```text
/photos                            # 只读照片源
/data/photos.db                    # SQLite 数据库
/data/output/photopainter          # 画廊渲染结果
/data/output/push                  # latest.bmp/latest.png/manifest.json
```

## 配置 .env

复制示例文件：

```bash
cp .env.example .env
```

至少修改这些字段：

```env
PHOTO_DIR=/vol1/你的照片目录
DATA_DIR=/vol1/docker/inktime/data

LOCAL_VLM_API_URL=http://你的电脑局域网IP:9100/v1/chat/completions
CLOUD_QWEN_API_KEY=你的云端API_KEY
INKTIME_PUSH_API_TOKEN=你自己的手动推送token
INKTIME_ADMIN_INITIAL_PASSWORD=至少8位的一次性初始密码
INKTIME_SESSION_SECRET=至少32位的随机会话密钥
```

注意：容器在 NAS 内运行，`127.0.0.1` 指的是 NAS 容器自己，不能代表你的电脑。局域网本地模型必须填写电脑的固定 IP。

首次登录后必须修改初始密码。忘记密码时执行以下命令，命令会注销全部已有管理员会话，并要求下次登录重新修改密码：

```bash
sudo docker compose exec web python -m flask --app server.py reset-admin-password
```

真实密码与会话密钥只能保存在 NAS `.env` 中，不要写入镜像、文档或 Git。

## 启动服务

```bash
docker compose build
docker compose up -d web scheduler analysis-worker
```

打开：

```text
http://<NAS-IP>:8766/renders
http://<NAS-IP>:8766/healthz
```

墨水屏设备固定读取：

```text
http://<NAS-IP>:8766/push/latest.bmp
```

浏览器调试预览：

```text
http://<NAS-IP>:8766/push/latest.png
```

## 按需任务

分析照片：

```bash
docker compose run --rm worker analyze_photos.py -j 1 --debug
```

批量渲染画廊：

```bash
docker compose run --rm worker render_photopainter.py
```

单次测试自动推送：

```bash
docker compose run --rm worker scheduler.py --run-once --slot 07:00
```

## 验证 BMP

在容器里检查最新 BMP：

```bash
docker compose run --rm worker - <<'PY'
from pathlib import Path
from PIL import Image
from photopainter_renderer import SIX_COLOR_PALETTE

path = Path("/data/output/push/latest.bmp")
with Image.open(path) as img:
    colors = set(img.convert("RGB").getdata())
    print("size:", img.size)
    print("mode:", img.mode)
    print("six_color_only:", colors <= set(SIX_COLOR_PALETTE))
PY
```

期望输出：

```text
size: (800, 480)
mode: RGB
six_color_only: True
```

## 运维命令

查看日志：

```bash
docker compose logs -f web
docker compose logs -f scheduler
```

重启：

```bash
docker compose restart web scheduler
```

停止：

```bash
docker compose down
```

更新代码后重新部署：

```bash
docker compose build
docker compose up -d web scheduler
```

## 安全边界

- `.env` 不要提交 Git，里面包含云端 API key 和推送 token。
- 照片目录按只读挂载，容器不会修改原图。
- `/push/latest.bmp` 不鉴权，方便墨水屏直接下载；不要把服务直接暴露到公网。
- 云端 key 只通过 `.env` 或飞牛 Docker 环境变量注入。
