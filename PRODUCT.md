# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

主要用户是本地照片库的维护者和推送操作者，使用电脑端浏览器管理家庭/生活照片，从已分析照片中发现适合墨水屏展示的内容，并在推送前微调文案、构图和六色渲染参数。

次要受众是后续接手开发的 agent 或维护者。他们需要理解本项目是一个本地/NAS 上运行的软件闭环，而不是 Windows 桌面应用或固件项目。

## Product Purpose

InkTime PhotoPainter Local 把本地照片目录变成一个可审核、可筛选、可编辑、可推送的 PhotoPainter 六色墨水屏内容库。

产品成功意味着：照片能被可靠扫描和 AI 分析，分析结果进入 SQLite；用户能在 WebUI 中按评分、日期、推送选片规则和随机发现浏览已分析照片；推送工作台能以六色 Canvas 预览构图，并在实际推送或定点推送时生成横框 `800x480` 或竖框 `480x800` PNG/BMP；最终写入 `/push/latest.bmp` 供设备拉取。

## Positioning

本项目不是通用相册，也不是单纯的图片转换器。它的差异点是把 AI 照片理解、回忆/美观评分、人工文案覆盖、设备一致六色渲染和 Web 推送工作台串成一个本地闭环。

FrameFilm 只作为单张选图、构图、参数调节和推送流程的交互参考；本项目不继承 `.film`、BLE、小程序或 FrameFilm 硬件协议。

## Operating Context

运行环境以 Windows 本地开发和飞牛 NAS Docker 部署为主。当前业务入口仍是 Python 脚本与 Flask Web 服务：

- `analyze_photos.py` 扫描照片、调用 OpenAI-compatible 视觉模型、写入 `photos.db`。
- `render_photopainter.py` 生成 PhotoPainter / Spectra 6 六色渲染结果。
- `server.py` 提供本地 WebUI、渲染浏览和 `/push/latest.bmp`。
- `scheduler.py` 可按时间触发自动推送。

模型通道保留本地 LM Studio 优先、云端 Qwen 兜底的双通道机制。真实 API key 留在 `config.py`、NAS `.env` 或环境变量中，不进入前端、不进入数据库、不进入公开文档。

第一阶段前端技术路线是 Flask JSON API + `static/app/` 原生模块化前端，不引入 React/Vite。当前 `docs/ui-mockup/` 是静态视觉草案，只用于确认交互和美术方向。

## Capabilities and Constraints

已确认能力：

- 状态中控台展示扫描、分析、GPS、缺失标记、最近推送和运行日志。
- 画廊只展示已经完成 AI 分析且默认存在于磁盘上的照片。
- 瀑布流支持综合分、拍摄日期、最近渲染和推送选片规则排序。
- 随机发现使用推送选片规则一致的候选逻辑，在综合分 60 分以上照片中重新洗牌；前端不单独暴露随机阈值输入。
- 画廊卡片以照片为主，`加入推送` 是悬停/聚焦动作，进入推送工作台继续设置。
- 批量选择不常驻画廊首屏；默认数量等偏好放在设置页，真正批量操作以后通过选择模式临时展开。
- 单张详情需要展示原图、AI 描述、短文案、评分、理由、模型通道、人工覆盖和最近渲染/推送历史。
- 推送工作台以电脑端鼠标拖动平移、滚轮缩放为主要交互；相框横放/竖放与照片自身旋转是两个独立参数。
- AI 原始字段保留；人工微调写入覆盖字段，例如 `custom_side_caption`、`manual_crop_json`、`render_overrides_json`。
- 工作台使用前端六色 Canvas 提供接近设备成品的即时预览；只有实际推送或定点推送才生成最终 PNG/BMP，不维护独立的旧式预览成品缓存。
- 扫描监控目录时缺失照片只标记 missing，不自动硬删除数据库记录。

硬边界：

- 不做 Windows 桌面应用。
- 不恢复 ESP32、Arduino、`.ino`、GxEPD2、NVS、Deep Sleep、固件烧录或设备刷写链路。
- 不新增 `.bin` 输出或 `/static/inktime/<key>/latest.bin`。
- 不把 API key 放入前端、数据库、测试或公开文档。
- 不用旧 manifest 数组下标作为新系统 ID；新 API 以 `photo_id` 为核心。
- 长任务不在单个 HTTP 请求中跑完；后续以任务表和 worker 承载。

开放决策：

- `/library` 与 `/analysis` 是否拆成两个页面，还是先合并为素材扫描页。
- 完整任务队列的暂停/继续/停止语义和 NAS worker 消费方式仍需实现时确认。

## Brand Commitments

产品名沿用 InkTime / PhotoPainter Local。当前 WebUI 视觉标题使用 `Warmth Archive` 作为英文装饰语，中文主文案为“收集散落的人间暖意，定格每一段时光”，小字为“照片瀑布流｜批量预览｜设备推送”。

语气应保持中文优先、清楚、温和、工具化。页面是让用户完成审核、预览和推送的工作台，不做营销落地页，不复制老式 Windows 桌面应用外观。

## Evidence on Hand

可用证据与资产：

- `docs/refactor-plan.md`：当前重构计划和阶段边界。
- `docs/ui-mockup/index.html` 与 `docs/ui-mockup/styles.css`：已确认接近目标的静态 WebUI 视觉草案。
- `sample_photos/`：静态稿使用的真实照片素材示例。
- `output/photopainter/`：设备风格渲染预览示例。
- `README.md`：当前软件闭环、运行方式、双模型通道和推送说明。
- `AGENTS.md`：项目边界、NAS Docker、测试和安全约束。

没有可公开使用的真实 API key、真实用户数据授权说明或外部客户证明。未来文案不得编造用户规模、商业证明或未验证的设备兼容性。

## Product Principles

1. 保持软件闭环清晰：扫描、分析、入库、渲染、预览、推送，每一步都可观察、可重试。
2. 照片优先，控制克制：画廊首先用于发现照片，复杂操作进入详情或推送工作台。
3. 设备一致性优先于装饰：预览必须尽量还原最终横框 `800x480` 或竖框 `480x800` 六色成品；最终文件尺寸包含底部文字条。
4. 原始 AI 结果不可被人工覆盖抹掉：人工调整以覆盖字段记录，保持可追溯。
5. 本地隐私和密钥安全优先：照片、数据库、输出和密钥默认留在本地/NAS，不进入 Git 或前端。

## Accessibility & Inclusion

当前产品主要面向电脑端浏览器操作，但应保留键盘聚焦状态、足够文字对比度和可读中文字体。悬停才出现的操作也必须在键盘聚焦时可见。移动端需要可浏览和基础操作，但复杂构图以桌面鼠标交互为优先。
