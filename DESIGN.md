---
name: "InkTime PhotoPainter Local"
description: "照片到墨水屏的本地 Web 工作台"
colors:
  ink: "#151813"
  muted: "#68716a"
  muted-deep: "#4c5a51"
  caption-muted: "#72796f"
  page: "#f7f8f4"
  surface: "#ffffff"
  soft: "#eef1ec"
  soft-green-line: "#d8e8dc"
  meter-track: "#d7ded5"
  line: "#dfe4dc"
  line-strong: "#cbd3c7"
  rail: "#1c211b"
  green: "#26a96c"
  green-dark: "#147448"
  blue: "#2477d4"
  cyan: "#52aeb8"
  yellow: "#e4b536"
  red: "#d94d40"
  push-red: "#e72f4c"
typography:
  display:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "clamp(32px, 5vw, 58px)"
    fontWeight: 800
    lineHeight: 1.02
    letterSpacing: "0"
  script:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: "28px"
    fontWeight: 700
    lineHeight: 1
  body:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "14px"
    lineHeight: 1.55
  label:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "12px"
    fontWeight: 800
  micro:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "10px"
  panel-title:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "17px"
    fontWeight: 800
  nav-mark:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "18px"
    fontWeight: 800
  section:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "clamp(25px, 3vw, 36px)"
    fontWeight: 800
  status-display:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "clamp(24px, 3vw, 42px)"
    fontWeight: 800
  metric:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "34px"
    fontWeight: 800
  mobile-display:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "30px"
    fontWeight: 800
  mobile-script:
    fontFamily: 'Georgia, "Times New Roman", serif'
    fontSize: "22px"
    fontWeight: 700
  mobile-section:
    fontFamily: '"Segoe UI", "Microsoft YaHei", Arial, sans-serif'
    fontSize: "26px"
    fontWeight: 800
  caption-strip:
    fontFamily: '"KaiTi", "STKaiti", "Microsoft YaHei", serif'
    fontSize: "14px"
rounded:
  inset: "9px"
  control: "10px"
  note: "12px"
  card: "14px"
  panel: "16px"
  pill: "999px"
spacing:
  xs: "6px"
  sm: "10px"
  md: "14px"
  lg: "18px"
  xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.green}"
    textColor: "{colors.surface}"
    rounded: "{rounded.control}"
    padding: "9px 14px"
    height: "40px"
  button-ghost:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "9px 14px"
    height: "40px"
  card-photo:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.card}"
---

# Design System: InkTime PhotoPainter Local

## Overview

**Creative North Star: "Warmth Archive Operations Desk"**

InkTime PhotoPainter Local 的界面是一张安静的照片工作台：它有家庭照片的温度，也有本地工具应有的秩序。设计不复制传统 Windows 桌面应用，也不把自己包装成营销页；它让用户快速知道系统状态、浏览已分析照片、进入单张推送工作台，并确信预览会接近设备最终成品。

视觉系统使用浅色矿物纸面、深墨绿色侧栏、强黑中文标题和少量 PhotoPainter 六色点缀。照片是主角，控件退到恰好可用的位置；危险或强动作才使用高饱和红色，日常成功和主流程使用绿色。

**Key Characteristics:**

- 照片优先的操作界面，而不是功能按钮堆叠。
- 浅色纸面与深色侧栏形成稳定工作区。
- 中文大标题有记忆感，工具控件保持清楚紧凑。
- 设备输出、AI 状态和人工覆盖都必须可追溯。

## Colors

调色板以暖白纸面、墨黑文字和工作型绿色为基础，辅以 PhotoPainter 六色的红、黄、蓝、绿作为状态和设备语境提醒。

### Primary

- **Archive Green** (`green`): 主操作、成功状态、进度条起点和品牌生命感。
- **Deep Archive Green** (`green-dark`): 小标题、状态文字、次级强调和深色侧栏里的品牌延伸。

### Secondary

- **Device Blue** (`blue`): 进度条终点和设备/渲染相关的冷色提示。
- **Mineral Cyan** (`cyan`): 进度过渡、轻量系统状态和六色墨水屏语境。

### Tertiary

- **Push Red** (`push-red`): 画廊卡片上的“加入推送”等强动作。只在用户表达推送意图时出现。
- **Warning Red** (`red`): 缺失、失败和需要人工处理的状态。
- **Spectra Yellow** (`yellow`): 保留给六色调色板、标签或未来渲染说明，不作为大面积背景。

### Neutral

- **Ink Black** (`ink`): 主文字和高对比标题。
- **Muted Moss** (`muted`): 辅助说明、元信息和非关键日志。
- **Paper Page** (`page`): 页面背景。
- **Clean Surface** (`surface`): 卡片、面板、输入框和照片信息底。
- **Soft Mineral** (`soft`): 控件底、分段控件和轻量占位。
- **Fine Line** (`line`) / **Strong Line** (`line-strong`): 分隔、输入描边和弱边界。
- **Night Rail** (`rail`): 左侧导航背景。

### Named Rules

**The Photo-First Accent Rule.** 红色推送按钮默认不压在照片上，只有悬停或键盘聚焦时出现。

**The Green Is Work Rule.** 绿色用于主流程、可用和成功，不用于装饰性大面积铺色。

## Typography

**Display Font:** Segoe UI / Microsoft YaHei / Arial  
**Body Font:** Segoe UI / Microsoft YaHei / Arial  
**Script Accent:** Georgia / Times New Roman italic

**Character:** 中文标题要有力量和温度，像给个人照片档案命名；正文和控件必须像工具一样直接、可扫读。英文只作为轻量装饰或系统分区名，不抢中文信息主位。

### Hierarchy

- **Display** (800, `clamp(32px, 5vw, 58px)`, 1.02): 顶部产品宣言和第一视口识别。
- **Section Headline** (800, `clamp(25px, 3vw, 36px)`, 1.12): 页面主模块标题，例如状态中控台、画廊、推送工作台。
- **Panel Title** (700-800, 17px, 1.25): 卡片、参数组和日志区标题。
- **Body** (400, 14px, 1.55): 说明、日志、元信息和表单内容。
- **Label** (760-850, 12px, normal tracking): 状态标签、kicker、chip 和小型按钮。
- **Script** (italic 700, 28px, 1): `Warmth Archive` 这类英文装饰，只用于品牌气质。

### Named Rules

**The Chinese Leads Rule.** 功能、状态和操作使用中文优先；英文只做辅助气质和分区节奏。

## Layout

桌面端采用固定左侧导航 + 右侧工作区。工作区顶部是品牌标题，中部按 hash route 切换页面：状态中控台、画廊、推送工作台、设置页。页面不是从上到下混排的长落地页，配置等模块必须是独立页面。

状态中控台使用一条宽状态 band 展示当前结论，下方用统计卡和操作/最近推送/日志三列组织。画廊使用三列瀑布流，宽屏保持照片密度；中等屏幕变两列，移动端变一列。推送工作台使用左侧设备预览、右侧参数面板；移动端堆叠。

间距保持工具型节奏：小控件间距 6-10px，面板内部 14-18px，页面段落 28px 以上。不要为了装饰制造额外分区。

## Elevation & Depth

系统使用柔和但真实的阴影来区分可操作面板和照片卡片。背景本身保持平，不使用装饰性光斑、渐变球或虚假的玻璃拟态。阴影承担层级和悬停反馈，不作为花纹。

### Shadow Vocabulary

- **Panel Shadow** (`0 18px 44px rgba(39, 48, 42, 0.11)`): 照片卡片、统计卡、普通面板。
- **Deep Shadow** (`0 28px 80px rgba(39, 48, 42, 0.18)`): 设备预览、评分圆标、需要浮起的关键内容。
- **Action Shadow** (`0 8px 18px rgba(38, 169, 108, .22)`): 主按钮。
- **Push Shadow** (`0 12px 28px rgba(231, 47, 76, .28)`): 悬停出现的推送动作。

### Named Rules

**The Shadow Has a Job Rule.** 阴影只表示层级、状态或可操作性；不能用来给空白区域制造装饰。

## Shapes

形状以轻微圆角为主，保持现代 Web 工具的柔和但不过分可爱。普通控件使用 10px，照片卡片 14px，大面板 16px，状态 chip 和浮动推送按钮使用 pill。评分圆标是少数强几何元素，用来像设备徽章一样快速识别综合分。

边界优先用背景、间距和阴影区分，只有输入框、ghost button 和细分隔需要 1px 线。

## Components

### Buttons

- **Shape:** 稳定圆角控制（10px），小按钮高度 34px，标准按钮最小高度 40px。
- **Primary:** Archive Green 背景、白字、轻阴影；只用于当前流程最推荐动作。
- **Ghost:** 白色背景、Ink Black 文本、1px 内描边；用于普通导航和次级命令。
- **Text:** 透明背景、Deep Archive Green 文本；用于折叠、轻量编辑等不改变主要流程的动作。
- **Push Float:** Push Red 背景、pill 形状，默认隐藏，照片卡片悬停或键盘聚焦时出现。

### Chips

- **Style:** 浅绿色底、Deep Archive Green 文本、pill 形状。
- **Use:** 状态、监控目录、模型通道和可用性。不要把长说明塞进 chip。

### Cards / Containers

- **Photo Card:** 白底、14px 圆角、柔和阴影、真实照片占主要面积。卡片文案只保留标题、地点/日期/类型和综合分。
- **Status Band:** 浅绿色到青色的非常弱渐变，用于展示当前系统结论。
- **Metric Tile:** 白底、紧凑数字和说明，用于状态中控台。
- **Control Group:** 白底 16px 面板，用于推送工作台右侧参数组。

### Inputs / Fields

- **Style:** 白底、10px 圆角、Strong Line 描边。
- **Focus:** 未来实现时使用绿色边框或 outline，不改变控件尺寸。
- **Range:** 使用系统 range 控件，accent-color 为 Archive Green。
- **Textarea:** 用于人工文案覆盖，宽度占满所在参数组。

### Navigation

左侧 rail 是深色固定导航，活动项为暖白底和深色文字。窄屏时 rail 收缩为图标；移动端变为横向滚动导航。导航标签保持简短，不承载解释文案。

### Device Preview

设备预览固定表达 `800 × 480` 的横屏 PhotoPainter 成品。预览区包含上方图像和底部文字条，文字条使用中文衬线/楷体 fallback 来接近最终设备输出的温度。

## Do's and Don'ts

### Do:

- **Do** 让照片卡片中的照片占据主要视觉面积，控件只在需要时出现。
- **Do** 把扫描、分析、生成预览等系统级动作放在状态中控台，不在画廊重复。
- **Do** 把批量选择默认数量放在设置页，画廊只在进入选择模式后展开批量操作。
- **Do** 使用真实照片或真实渲染结果作为主要视觉资产。
- **Do** 保持配置页、画廊和推送工作台为独立页面。
- **Do** 在桌面端优先考虑鼠标拖动、滚轮缩放和精确参数输入。

### Don't:

- **Don't** 复制传统 Windows 桌面程序的密集按钮矩阵和厚重灰色面板。
- **Don't** 把配置页混在其他页面底部。
- **Don't** 在画廊里长期显示满屏红色推送按钮。
- **Don't** 在画廊首屏常驻“全选当前筛选 / 选择前 N 张”这类批量控制。
- **Don't** 用独立阈值输入干扰随机发现；随机候选遵循推送选片规则和 60 分以上口径。
- **Don't** 编造设备能力、公网能力、用户证明或 API key 状态。
