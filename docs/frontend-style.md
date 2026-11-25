# FinOracle 前端风格

FinOracle 采用**新瑞士平面 × 工业编辑风（Neo-Swiss Editorial）**：暖纸白画布 `#F4F4F2`、纯白表面 `#FFFFFF`、墨黑主色 `#161616`、结构细线 `#DDDDDD` 与唯一强调色信号黄 `#FFFA00`，辅以近黑顶栏和浅斜线工作区。西文与数字优先使用 `Saira Variable` 并开启等宽数字（`tabular-nums`），中文回退至 `PingFang SC` / `Microsoft YaHei`；导航及管理列表按序编号，管理分页统一显示为 `02 / 010`。界面随系统切换明暗主题，动效统一使用 `cubic-bezier(0.22, 1, 0.36, 1)` 并遵循系统的减少动态效果设置。

- 全站零圆角（`0px`）；主操作与关键面板采用 `clip-path` 右下切角，重要面板与弹窗顶部用 `3px solid #161616` 锚定，常规卡片默认无厚重投影，仅在精确指针设备悬停时轻幅上浮。
- 提示与语义反馈使用低饱和纸感色板：提示条为暖纸底 `#EEEED7` 配左侧 `3px` 墨黑竖线，警告为暖米黄 `#F5F4D4`，错误与危险操作使用克制砖红 `#B84A37`；状态保留文字标签，完成态深底黄字，待处理描边，失败态深底斜纹。
- 导入列表按当前页内的文档版本合并文件信息，组内按时间展示处理阶段。任务筛选、总数和分页仍以处理任务为单位，各阶段独立保留校验和重试。已知进度按十格分段条显示，并保留实际计数。
- 表格的日期、计数和操作保持单行。窄屏可在表格内横向滚动，导入阶段在文件下方纵向排列。

设计参考见 [终末地官网](https://endfield.hypergryph.com/#home)、[ignoredone.space](https://www.ignoredone.space/) 和 [endfield-ui](https://github.com/hhikr/endfield-ui-skill)。
