# 浏览器验收

测试采用 Chromium 156、Playwright 1.65 和 Node.js 24。桌面视口覆盖 1440 × 900、1440 × 960 与 1488 × 1058，手机视口为 390 × 844。测试读取 Vite 生产构建，并通过同源 HTTP 访问 FastAPI 和临时 SQLite。测试进程使用独立账户。可重复回归中的模型响应、执行事件和沙箱文件来自测试夹具，以便稳定复现取消、失败和断线场景。真实模型与 Docker 任务另有逐次执行记录。

## 通用工作台与渠道销售小结

本次完整 Playwright 套件通过 8 项，耗时 45.8 秒，含真实制粒机归档 GLB、旧执行记录及工具截断标记回归。流式测试按片段推进。首段文字到达时，下一段与最终答复尚未写入 SQLite。测试覆盖刷新后的重放、SSE 连接中断后的按游标补读、执行错误的退出码、子智能体工具的输入与返回，以及最终答复去重。历史执行记录沿用原先的无结构化字段格式。同一套件还验证了登录、手机导航、跨用户文件隔离、历史长标题的缩短，以及 Markdown 正文和原文切换。正文中的原始 HTML 与外部配图向测试域发送了 0 个请求。

真实部署另外执行了四笔订单的渠道汇总。首次运行的 reviewer 检出占比的舍入差异，自身的正则检查也曾因未匹配 Markdown 中的总计行退出。后续检查和编辑将合作渠道占比修为 28.6%，补充了合计占比 100.1% 的舍入说明。首轮浏览器测试在下载链接出现时即读面板，早于异步文件正文。原始报告和首次观测均已保留。

[首轮执行与断言记录](live-timeline-first.json) · [首轮生成的 Markdown](channel-summary-first.md)

第二次通过浏览器提交同一题，真实模型和 Docker 沙箱在 115 条事件后返回完成。前 3 个公开文字分片位于游标 1071–1073，随后调用 `write_file`。共收到 70 个文字分片，10 次工具调用全部成功，reviewer 内部占 7 次。浏览器刷新后重放了正文，展开工具能看到输入与返回值。测试独立下载并解析了 4 笔 CSV 订单，核对 Markdown 中门店 1700 元、线上 800 元、合作 1000 元、合计 3500 元，并等待文件面板显示报告的标题、表格和合计。12 个检查均通过，浏览器脚本异常为 0。

[运行中的正文与工具参数](../screenshots/live-trace-running.webp) · [完整时间线与嵌套复核](../screenshots/live-trace.webp) · [报告预览](../screenshots/live-report-preview.webp) · [真实模型记录](live-timeline.json) · [生成的 Markdown](channel-summary.md)

## 历史制粒机浏览器验收

| 用例 | 观察结果 |
| --- | --- |
| 身份、会话、三级执行树与文件 | 注册、退出后登录、页面刷新、切换历史会话和新建会话均通过。展开子智能体可查看工具。自包含的双网格 GLB 在 WebGL 中加载，支持放大预览、爆炸滑块、视角重置与下载。联轴器实测中发现的爆炸后取景裁切已修复。按当前零件位置重算边界后，100% 爆炸状态的视角重置通过像素回归；相同测试在旧实现上失败。CSV 保留引号包围的逗号字段，可切换原文；HTML 以文本显示。 |
| 取消与失败 | 折叠的执行摘要持续显示当前工具。用户停止执行后，界面显示已停止，输入框可提交后续任务。人为触发的运行失败显示后端错误。 |
| 手机操作与重试 | 会话菜单、文件面板、键盘提交可用。文件列表首次返回 503，刷新后恢复。手机页面未横向溢出。 |
| 跨账户与外链 GLB | 使用两个独立浏览器上下文登录不同用户。另一用户请求已知文件地址得到 404，会话列表为空。含网络资源引用的 GLB 被拒绝，浏览器向模型声明的外部地址发送 0 个请求。 |

最后一轮常规回归通过 4 项，耗时 22.2 秒。npm audit --omit=dev 在当次生产依赖锁定版本上返回 0 个已知漏洞公告。页面自动检查包含文档标题、工作台内容和浏览器脚本异常。应用在首次匿名探测账户时会收到预期的 HTTP 401。Three.js 预览模块仅在打开 GLB 后加载。

Compose 部署中的制粒机任务已在浏览器内观察到官方文档 MCP、文件读取、沙箱命令和模型轮次的实时状态。顶层显示当前步骤，展开后可以检查每个节点。

[实际运行中的执行树](https://github.com/zlstart/codex_deepagents/blob/ecc5193bdd69ad4add0d539d77caefc527936669/docs/screenshots/live-trace.webp)

## 在线制粒机文件复测

首次展开同一制粒机会话时，文件列表探针等候 30 秒超时，选中 GLB 后 60 秒内未出现预览控件。沙箱原来为每次只读操作启动完整科学计算镜像。部署切换到独立轻量读取器后，通过同一测试用户重新打开两个原始 GLB，同一浏览器上下文中串行读取成功。将这次 HTTP 响应中的真实 pellet.glb 用作额外回归输入后，完整浏览器套件 5/5 通过，耗时 25.8 秒。原首次运行达到执行时限，服务保留了已经生成的文件供后续回合读取。

| 检查 | 浏览器观测 |
| --- | --- |
| 装配和独立爆炸文件 | 两份自包含 GLB 都进入 Three.js 预览，各有 6 个网格。 |
| 旋转与爆炸 | 拖动改变画布。相同视角下滑块从 0% 调至 100%，1137 × 710 画布内有 330,998 个像素的至少一个 RGB 通道变化超过 12。 |
| 爆炸后重置视角 | 可见模型边界位于 x=301–723、y=216–533，完整落在画布内。 |
| 客户端异常 | 浏览器脚本错误 0 条，外部 HTTP 请求 0 条。 |

[初次请求记录](browser-first-read.json) · [在线复测数据](pellet-preview.json) · [装配态](../screenshots/pellet-assembled.webp) · [模型自带爆炸图](../screenshots/pellet-exploded.webp) · [爆炸预览工作台](../screenshots/pellet-preview.webp)

## 复测

先在仓库根目录执行 `uv sync --locked --group eval`。随后运行：

```sh
cd web
npm ci
npm run build
npx playwright install --with-deps chromium
npm test
```

已有本地运行的部署、可注册账户以及配置好的模型时，从仓库根目录运行以下独立验收。脚本会注册一个临时测试账户并实际提交一次模型任务，退出时注销登录凭据。需要可使用 Pillow 的 Python 环境，将截图转换为 WebP。

```sh
FAMSUN_E2E_PYTHON="$PWD/.venv/bin/python" \
  node web/scripts/live-timeline.mjs http://127.0.0.1:8080 /tmp/famsun-browser-evidence
```

可将 `FAMSUN_E2E_CHROMIUM` 设为系统 Chromium 的绝对路径。设置 `FAMSUN_E2E_MODEL` 为真实验收产生的制粒机 GLB 绝对路径，会额外测试该模型的网格数量、爆炸前后截图变化和外部请求。此项使用归档文件重放，截图保存在 `web/test-results`。

界面对照检查涵盖侧栏与工作区布局、中文标题层级、两幅任务插图、文件入口、固定输入框和移动菜单。与生成设计稿对照后，修正了桌面制粒机插图过小和轨道图被裁切的问题。

[桌面工作台](https://github.com/zlstart/codex_deepagents/blob/ecc5193bdd69ad4add0d539d77caefc527936669/docs/screenshots/workspace.webp) · [登录页](https://github.com/zlstart/codex_deepagents/blob/ecc5193bdd69ad4add0d539d77caefc527936669/docs/screenshots/login.webp) · [移动端](https://github.com/zlstart/codex_deepagents/blob/ecc5193bdd69ad4add0d539d77caefc527936669/docs/screenshots/mobile.webp)
