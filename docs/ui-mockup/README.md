# WebUI 静态设计稿

这是基于 `docs/refactor-plan.md` 产出的 UI 美术与交互结构 mockup，只用于视觉方向确认。

打开：

```text
C:\SUSAN\PARA\InkTime-PhotoPainter-local\docs\ui-mockup\index.html
```

页面入口：

- `index.html#dashboard`：状态中控台。
- `index.html#gallery`：画廊。
- `index.html#studio`：推送工作台。
- `index.html#settings`：配置页。

当前不实现真实功能：

- 不连接 Flask API。
- 不写数据库。
- 不执行扫描、分析、渲染或推送。
- 表单、按钮、滑杆仅展示交互状态。

设计范围：

- 状态中控台。
- 已分析照片瀑布流。
- 单张推送工作台。
- 配置页视觉草案。

后续实现时建议把视觉结构拆到 `static/app/`，再接入真实 API。
