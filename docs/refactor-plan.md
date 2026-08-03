# PhotoPainter WebUI 重构开发计划

## 目标

本次重构保持现有业务闭环不变：扫描照片、调用 OpenAI-compatible 视觉模型分析、写入 SQLite、按 PhotoPainter 六色墨水屏效果渲染、通过 WebUI 预览并生成 `/push/latest.bmp` 供设备拉取。

重构重点是把当前脚本式、manifest 驱动的页面，升级为数据库驱动的 Web 管理工作台：

- 首页状态中控台。
- 已分析照片瀑布流画廊。
- 单张详情与文案微调。
- 独立推送工作台。
- 推送工作台内即时六色 Canvas 构图预览，实际推送时生成最终设备文件。
- 渐进式数据库模型重构。
- 为后续批量 AI 分析、配置页和照片库管理预留接口。

## 已确认决策

### 手动推送链路

手动推送保持现有设备链路：用户在 WebUI 选择照片、调整构图/文案/渲染参数，后端生成 `output/push/latest.bmp` 和 `output/push/latest.png`，设备继续通过 `/push/latest.bmp` 拉取。

`FrameFilm-main` 只作为交互参考：选图、构图调整、转换参数、预览、推送。不要引入 `.film` 文件、BLE、小程序或 FrameFilm 硬件协议。

### 前端技术路线

第一阶段使用 Flask JSON API + `static/app/` 原生模块化前端，不引入 React/Vite。

原因：

- 当前 NAS/Docker 单 Python 镜像已经跑通。
- 原生 JS 足够支撑瀑布流、批量选择、状态轮询、参数编辑和推送工作台。
- 避免第一阶段引入 Node 构建链路和 Docker 复杂度。

### 信息架构

- `/`：状态中控台。
- `/gallery`：数据库驱动瀑布流画廊，只展示已完成 AI 分析的照片。
- `/photos/<photo_id>`：单张详情页。
- `/push-studio/<photo_id>`：单张推送工作台。
- `/library` 或 `/analysis`：素材扫描与待分析管理，第二阶段完善。
- `/settings`：配置页，第二阶段完善。

旧路由保留兼容：

- `/renders` 临时重定向或兼容到新画廊。
- `/review` 临时重定向或兼容到分析/素材页。
- `/render/<id>` 不再作为主入口，避免旧 manifest 下标造成混乱。

### UI 方向

只做 WebUI，不做 Windows 桌面应用。参考桌面工具图的信息结构，不复制它的老式桌面外观。

第一版 UI 改为浅色/中性色、照片优先、现代 Web 工具风格：

- 首页是清晰的状态中控台，不做营销页。
- 画廊以照片为主视觉，用瀑布流和工具栏组织操作。
- 日志区可折叠，不占主要页面。
- 推送工作台采用左侧大预览、右侧参数/文案/操作区。
- 操作控件使用工具型 UI：筛选、排序、滑杆、输入框、按钮、状态标签。

### 画廊与素材页口径

画廊只展示 `photo_scores` 中已完成分析的照片。

未分析照片放到独立素材/扫描页面管理，用于查看监控目录总数、未分析数量、已分析数量，并发起批量 AI 分析任务。

### 渲染策略

六色设备成品预览采用按需生成 + 缓存。

- 画廊列表先显示原图缩略图和 AI 信息，不强制所有照片提前生成六色预览。
- 打开详情、进入推送工作台或点击生成预览时，后端按当前参数生成 PNG/BMP。
- 缓存由数据库表管理，而不是只靠文件名散落在目录中。
- 全量批量生成预览作为可选任务，不阻塞画廊打开。

### 文案与构图覆盖

AI 原始字段保留，不被人工修改覆盖。

新增人工覆盖字段：

- `custom_side_caption`：人工微调后的最终展示/渲染文案。
- `manual_crop_json`：人工构图参数。
- `render_overrides_json`：亮度、对比度、饱和度、抖动、版式等渲染覆盖参数。

渲染优先级：

1. 人工覆盖参数。
2. AI 结果，例如 `side_caption`、`crop_focus_json`。
3. 系统默认配置。

### 构图交互

主要面向电脑端 Web 操作。后续交互重点是鼠标拖动平移和滚轮缩放，不以移动端双指缩放为优先目标。

第一阶段 MVP 可以先做参数控制 + 后端预览：

- 填充裁切 / 完整留边。
- 缩放倍率。
- 水平/垂直偏移。
- 旋转。
- 亮度、对比度、饱和度。
- 抖动算法。
- 文案、日期、地点显示开关。

`manual_crop_json` 按后续鼠标操作可复用的数据结构设计：

```json
{
  "scale": 1.0,
  "offset_x": 0,
  "offset_y": 0,
  "rotation": 0,
  "fit_mode": "fill"
}
```

### 排序与随机发现

画廊排序支持：

- 综合分：`memory_score + beauty_score`。
- 拍摄日期。
- 最近渲染：按最近一次生成预览或推送成品时间排序。
- 随机发现 / 推送选片规则：优先“历史上的今天”，再考虑近期未推，最后以高分兜底；每次刷新或点击按钮重新洗牌。

随机发现候选只从综合分 `60` 以上的照片中产生。前端不要单独暴露随机阈值输入，避免和推送选片规则重复。

### 配置持久化

Web 配置页不写 `config.py`。

新增 `app_settings` 表保存可变配置；`config.py` / `.env` 只作为启动默认值和密钥来源。

第一版配置页不保存或展示真实 API key，只管理：

- 通道启用/禁用。
- 模型名。
- base URL。
- timeout。
- 连通性测试状态。

真实 API key 仍放在本地 `config.py`、NAS `.env` 或环境变量里。

### 任务执行

AI 分析、批量渲染等长任务不在单个 HTTP 请求里直接跑完。

后续采用 SQLite 任务表 + 后台 worker：

- Web API 只创建任务、控制任务状态、查询进度。
- 本地开发可先用 Flask 后台线程消费。
- NAS/Docker 最终形态用独立 `worker` 容器消费任务。
- 支持开始、暂停、继续、停止、进度和日志。

第一阶段只允许一个全局 AI 分析任务运行；后续再扩展多任务队列。

### 批量选择规则

手动勾选优先。

如果用户输入数量 N，则从当前筛选/排序后的集合顶部取 N 张。

批量选择控件不常驻画廊首屏；默认数量等偏好放入设置页，进入批量选择模式后再展开“全选当前筛选 / 选择前 N 张”等操作。

### 目录扫描和缺失照片

扫描监控目录时不自动硬删除数据库记录。

- 文件存在：更新 `photos.exists_on_disk = 1`。
- 文件缺失：标记 missing。
- 默认画廊隐藏 missing。
- 清理缺失记录作为显式操作，需要二次确认。

这样可以避免 NAS 挂载短暂异常导致 AI 分析结果被误删。

## 第一阶段 MVP 范围

第一阶段先打通主线，不一次性完成全部设想。

### 必做

1. 新增数据库迁移和兼容层。
2. 新增 `photo_id`，新 API 不再依赖 path 或 manifest 下标。
3. 首页状态中控台。
4. `/gallery` 数据库驱动瀑布流。
5. 画廊排序：综合分、拍摄日期、最近渲染、随机发现。
6. `/photos/<photo_id>` 单张详情。
7. 人工文案覆盖保存。
8. `/push-studio/<photo_id>` 单张推送工作台。
9. 实际推送或定点推送时生成最终六色 PNG/BMP。
10. 保留旧路由兼容。
11. 本地测试和最终 NAS/Docker 验收。

### 可选加速项

- 推送工作台加入鼠标拖动平移和滚轮缩放。
- 画廊支持批量选择 UI，但不接完整批量任务。
- 首页加入最近日志和最近推送状态。

### 延后到第二阶段

- 完整批量 AI 分析任务。
- 完整前端配置页。
- 批量渲染。
- 批量推送。
- 更完整的任务队列、暂停、继续和日志流。
- 鼠标拖动/滚轮缩放的完整 Canvas 编辑器，如果第一阶段未完成。

## 第一阶段不做范围

- 不做 Windows 桌面应用。
- 不引入 React/Vite。
- 不引入 BLE、`.film` 或 FrameFilm 硬件协议。
- 不恢复 ESP32 固件链路。
- 不把 API key 存数据库或前端。
- 不强制打开画廊前全量生成所有预览图。
- 不在单个 HTTP 请求中直接跑完整长任务。
- 不把旧 manifest 数组下标作为新系统 ID。
- 不扫描时自动硬删除缺失照片记录。
- 不覆盖 AI 原始文案和 AI 原始 crop focus。

## 数据库设计草案

第一阶段采用渐进式迁移：新增表和兼容层，暂时保留旧 `photo_scores.path` 主键，避免一次性打断现有脚本。

### photos

```sql
CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  file_hash TEXT,
  size_bytes INTEGER,
  mtime REAL,
  exists_on_disk INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'analyzed',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  missing_at TEXT
);
```

迁移逻辑：

- 从 `photo_scores.path` 补齐 `photos`。
- 新 API 使用 `photos.id`。
- 旧脚本继续通过 `photo_scores.path` 工作。
- 第二阶段再考虑把 `photo_scores` 正式迁移为 `photo_id` 外键。

### photo_overrides

```sql
CREATE TABLE IF NOT EXISTS photo_overrides (
  photo_id INTEGER PRIMARY KEY,
  custom_side_caption TEXT,
  manual_crop_json TEXT,
  render_overrides_json TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(photo_id) REFERENCES photos(id)
);
```

### render_assets

```sql
CREATE TABLE IF NOT EXISTS render_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  photo_id INTEGER NOT NULL,
  variant_hash TEXT NOT NULL,
  preview_png_path TEXT NOT NULL,
  bmp_path TEXT,
  render_params_json TEXT NOT NULL,
  caption_used TEXT,
  created_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL,
  UNIQUE(photo_id, variant_hash),
  FOREIGN KEY(photo_id) REFERENCES photos(id)
);
```

`variant_hash` 由以下内容共同生成：

- `photo_id` 或源文件状态。
- `custom_side_caption` / `side_caption`。
- `manual_crop_json` / `crop_focus_json`。
- 亮度、对比度、饱和度。
- 抖动算法。
- 版式、日期、地点显示开关。
- 渲染尺寸和文字区高度。

### app_settings

```sql
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### analysis_jobs / render_jobs

第二阶段完善任务表。第一阶段可以先预留迁移，不强制实现完整 worker。

```sql
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  selection_json TEXT,
  progress_current INTEGER NOT NULL DEFAULT 0,
  progress_total INTEGER NOT NULL DEFAULT 0,
  message TEXT,
  log_path TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
```

## API 草案

### 状态中控台

- `GET /api/status`
  - 监控目录。
  - 总照片数。
  - 已分析数。
  - 未分析数。
  - missing 数。
  - 当前模型通道状态。
  - 最近推送结果。
  - 当前任务状态。

### 画廊

- `GET /api/photos`
  - 参数：`sort`、`order`、`q`、`type`、`min_score`、`limit`、`cursor`、`random_seed`。
  - 返回已分析且存在的照片列表。

- `GET /api/photos/<photo_id>`
  - 返回原图信息、AI 结果、人工覆盖、最近渲染资产。

- `PATCH /api/photos/<photo_id>/overrides`
  - 保存人工文案、构图和渲染参数。

### 预览与推送

- 推送工作台使用前端 Canvas 即时展示当前构图和六色抖动效果，不单独保存预览资产。
- `render_assets` 表保留为兼容结构，当前阶段不把它作为推送工作台的必经链路。

- `POST /api/photos/<photo_id>/push`
  - 按当前参数生成设备成品。
  - 写入 `output/push/latest.bmp`、`latest.png`、`manifest.json`。
  - 写入 `push_history`。

### 素材扫描和任务

第二阶段完善：

- `POST /api/library/scan`
- `GET /api/library/summary`
- `POST /api/jobs/analysis`
- `POST /api/jobs/<job_id>/pause`
- `POST /api/jobs/<job_id>/resume`
- `POST /api/jobs/<job_id>/stop`
- `GET /api/jobs/<job_id>`
- `GET /api/jobs/<job_id>/logs`

## 页面草案

### 首页状态中控台

首屏内容：

- 当前状态卡：可分析、正在分析、暂停、失败等。
- 监控目录和照片数量。
- 已分析/未分析/missing/GPS 统计。
- 今日推荐或随机发现入口。
- 最近推送：时间、照片、预览、状态。
- 快捷操作：扫描、进入画廊、进入推送工作台、设置。
- 折叠日志。

### 瀑布流画廊

功能：

- 瀑布流展示所有已分析照片。
- 工具栏排序和筛选。
- 随机发现使用推送选片规则，并只从综合分 60 以上候选中洗牌；前端不单独展示阈值输入。
- 卡片展示原图缩略图、文案、日期、地点、类型、综合分。
- 入口：详情、推送工作台、生成预览。

### 单张详情

功能：

- 原图和最近设备预览。
- AI 原始描述、AI 短文案、评分、理由、模型通道。
- 人工文案编辑。
- 最近渲染/推送历史。
- 进入推送工作台。

### 推送工作台

功能：

- 左侧设备尺寸预览。
- 右侧参数面板。
- 文案编辑。
- 构图参数：模式、缩放、偏移、旋转。
- 渲染参数：亮度、对比度、饱和度、抖动。
- 显示开关：日期、地点、文案。
- 操作：生成预览、保存参数、推送。

## FrameFilm 参考边界

可参考：

- 单张转换工作台的信息结构。
- 上传/选图、构图、转换参数、预览、推送的操作顺序。
- 鼠标拖动构图、滚轮缩放这种电脑端交互。
- 自动配置、抖动选择、对比度滑杆等控件思路。

不可照搬：

- `.film` 输出格式。
- BLE 传输。
- 小程序链路。
- FrameFilm 设备协议。
- 竖屏面板假设。

本项目设备输出仍以 PhotoPainter 六色 BMP 为准。

## 验收标准

### 本地验收

```powershell
python -m py_compile analyze_photos.py photopainter_renderer.py render_photopainter.py push_manager.py server.py
python -m unittest discover -v
python server.py
```

页面/API 验收：

- `http://127.0.0.1:8765/` 显示状态中控台。
- `/gallery` 能按数据库显示已分析照片。
- `/photos/<photo_id>` 能显示详情和保存人工文案。
- `/push-studio/<photo_id>` 能生成预览并推送。
- `/push/latest.bmp` 返回设备可用 BMP。

### NAS/Docker 最终验收

第一阶段最终需要纳入 NAS/Docker 验收，但开发过程以本地 Flask 验证为主。

至少验证：

```text
http://192.168.31.115:8766/healthz
http://192.168.31.115:8766/
http://192.168.31.115:8766/gallery
http://192.168.31.115:8766/push/latest.png
http://192.168.31.115:8766/push/latest.bmp
http://192.168.31.115:8766/push/manifest.json
```

`latest.bmp` 必须满足：

- 横框为 `800x480`，竖框为 `480x800`，尺寸均包含底部文字条
- `RGB`
- 只包含 PhotoPainter 六色调色板
- manifest 的 `image_url` 是 `/push/latest.bmp`

> 后续交互决策：推送工作台使用前端 Canvas 即时展示六色构图，不再保留独立的旧式渲染成品预览流程；最终 PNG/BMP 仅在手动推送或定点推送时生成。相框方向与照片旋转分别保存。

## 开发顺序建议

1. 新增数据库迁移模块和测试。
2. 建立 `photo_id` 查询兼容层。
3. 新增 JSON API：状态、照片列表、照片详情。
4. 改造 `push_manager.py` 支持 `photo_id` 和人工覆盖参数。
5. 新建 `static/app/` 原生前端骨架。
6. 实现首页状态中控台。
7. 实现 `/gallery` 瀑布流和排序。
8. 实现详情页和文案编辑。
9. 实现 `/push-studio/<photo_id>` 工作台和 Canvas 交互。
10. 保留旧路由兼容。
11. 补齐测试、本地运行验证和 NAS/Docker 验收。
