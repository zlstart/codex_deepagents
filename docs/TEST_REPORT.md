# FAMSUN Agent 部署与验收

## 2026 年 11 月 21 日 · 会话删除、文件预览与本地 Langfuse

本轮在 Linux 单节点的 Compose 工作台上复测。Office 验收通过产品 API 提交给真实第三方模型，浏览器使用普通账号访问实际生成的文件。Langfuse 使用本机独立项目，专用 FAMSUN 进程仅导出本轮追踪验收任务。

| 验收对象 | 操作与结果 |
| --- | --- |
| 会话删除与隔离 | 模型在一次性会话生成标记文件。删除返回 204，原先的 1 个运行、17 个事件、14 个检查点、25 条检查点写入和 2 个持久卷均已清空。相邻会话的原文件 SHA-256 保持一致。浏览器另测取消确认、运行中删除被拒及删除后刷新。[API 与物理资源记录](testing/office-release.json) · [删除对话框](screenshots/session-delete.webp) |
| 真实 Office 产物 | 同一次任务交付三页 PPTX、两页 DOCX、双工作表 XLSX 和静态 HTML。独立校验确认渠道明细、40 件受理、35 件办结和文件结构。实际浏览器打开四份文件，PPT 翻到第二页，HTML 呈现中文表格。运行记录包含 110 个事件、4 次工具失败，模型随后完成交付。已将 python-docx 和 python-pptx 加入沙箱，使用全新离线容器创建并重新读取 Word、PowerPoint 和 Excel。[幻灯片](screenshots/workspace-slides.webp) · [Word](screenshots/workspace-word.webp) · [Excel](screenshots/workspace-sheet.webp) · [HTML](screenshots/workspace-html.webp) |
| 格式和范围 | 对 DOC/DOCX、PPT/PPTX、XLS/XLSX 的 12 页逐页独立提取文字并核对顺序。另验证 RTF、ODT、ODS、ODP、原生 PDF、旧版 Excel 日期、公式缓存、可见工作表、80 行分页和 48 列限制。真实二进制 Word 冷预览约 9.9 秒，缓存命中约 0.6 秒，数据来自本次 VFS 主机单次采样。[格式测试](../tests/test_office_formats.py) · [表格边界测试](../tests/test_sheet_bounds.py) |
| 执行过程与界面 | 收起后只呈现已保存的最终答复及运行摘要。工具间发言、参数、结果、子智能体随过程折叠。覆盖流式增量先后到达、断线恢复、刷新、失败、取消、预算收尾和历史记录。桌面和手机均保留可读正文，系统要求减少透明度时改用实色。[收起效果](screenshots/trace-collapsed.webp) · [运行中](screenshots/live-trace-running.webp) · [手机工作台](screenshots/glass-mobile.webp) |
| 五项真实通用任务 | 直接问答、备忘读写、CSV 汇总、会议行动清单及子智能体独立复核均通过，五次最终运行共 104 个事件，SSE 逐条匹配 SQLite 重播，文档 MCP 调用为 0。独立复核首轮的一条 Python 检查命令拆分等式时失败，模型修正后交付了正确内容；相同输入复测通过全部工具与产物断言，原始失败记录已保留。[分项结果](testing/workspace-observability-release.json) |
| 本地 Langfuse | 实际登录 4.83.0 项目，运行一次带沙箱命令的计算，得到 12103。按 Trace ID 从 v2 observations API 读回 14 条记录，包括 2 次模型调用及 1 次工具调用，用户与会话标识一致，正文采集关闭。重启 Web 和 Worker 后同一 Trace 仍可检索。另以 OTLP 接收器验证并行子智能体和故障分支。[真实 Trace 界面](screenshots/langfuse-local-trace.webp) · [本地项目实测](../deploy/langfuse/verification.json) · [子智能体与错误导出](testing/langfuse-otel.json) |

本轮在开启真实 Docker 测试时通过 **142 项 Python 测试**，在开启 Office 转换和实际 GLB 浏览器回放时通过 **15 项 Playwright Chromium 测试**。前端生产构建、Ruff 及部署镜像检查通过。

本机使用 rootless Docker 和 VFS，沙箱功能验收显式放宽了缺少 cgroup 委派的检查。为节省 VFS 磁盘，本机 Langfuse 服务实际运行官方 amd64 镜像的合并文件系统，交付的 Compose 固定上游多架构镜像摘要。运维手册包括 HTTPS 接入、离线镜像准备和一致性备份步骤。本次验证了服务重启后的记录持久化，生产主机的配额、备份恢复和并发容量仍需按当地环境演练。[本地部署手册](../deploy/langfuse/README.md) · [部署预检](testing/document-preview-preflight.json)

## 2026 年 11 月 21 日 · 通用工作台与流式执行

本次在同一台测试机上更新 Docker Compose，保留已有账户、SQLite 历史记录和工作区卷。使用真实 OpenAI 兼容模型 `qwen3.8-flash`，浏览器通过与产品相同的 HTTP 和 SSE 接口访问服务。

| 验收对象 | 实测结果 |
| --- | --- |
| 品牌与布局 | 对照提供的 2048 × 953 门户图核对左侧立体主图、右侧浮起登录卡、右上字标与留白，另在 390 × 844 手机上检查登录和输入。工作台使用通用任务入口。[桌面登录](screenshots/login.webp) · [工作台](screenshots/workspace.webp) · [手机登录](screenshots/login-mobile.webp) |
| 五项通用任务 | 实际完成直接问答、文本备忘、CSV 汇总、Markdown 行动清单和独立复核。首轮 4/5 满足全部断言。备忘任务先并发写入和读取同一文件，首次读取失败，模型自行恢复。补充读写顺序指令后按原题复测，1 次写入和 1 次读取成功，下载文件的内容与 SHA-256 符合要求。五项最终记录合计 101 条事件，SSE 与 SQLite 重播逐条一致，文档 MCP 调用数为 0。[逐次结果、诊断和产物校验](testing/general-workspace.json) |
| 浏览器实际运行 | 以普通账户提交四笔渠道订单。执行完成前页面已显示模型的汇总计划。写文件卡片可分别展开路径、CSV 内容和工具返回，复核分支可继续展开。复测记录 115 条事件、70 个公开文本分片，10 次工具执行全部成功，其中 7 次位于复核子智能体下。在面板内打开模型生成的 Markdown，并独立核对 4 笔订单、三项渠道金额与 3,500 元总额。12 项检查通过。首轮的工具错误、模型自行修正与异步预览测试的修订也已保留。[过程截图](screenshots/live-trace-running.webp) · [嵌套执行](screenshots/live-trace.webp) · [报告预览](screenshots/live-report-preview.webp) · [两轮实测](testing/BROWSER.md) |
| 官方文档 MCP | 在单独开启集成的实例中，第一轮按要求查询 Deep Agents 文档并保存链接，第二轮仅用 `read_file` 读回文件、从对话恢复校验码。两轮均完成，工具全部成功。[两轮真实记录](testing/on-demand-docs.json) |
| 流式传输 | TCP 测试在任务结束前读取首段，断开连接后再写入事件，携带最后序号重连，逐项比对回放与分页结果。另验同源与账户归属、注销撤销、迁移已有 SQLite 库、超大预览和终态事件。浏览器覆盖 SSE 和断流后的分页补读。[传输测试](../tests/test_api_stream.py) · [渲染规则与参考源码](EXECUTION.md) |

Python 最终整套回归通过 **102/102**，启用 `FAMSUN_REQUIRE_DOCKER_TESTS=true`，耗时 117.63 秒。覆盖真实 Docker 工作区、SQLite 迁移、租户隔离、流式模型回调、非流式兼容端点、工具返回与失败、预算收尾及会话恢复。

Chromium 自动化套件在桌面和手机视口通过全部 8 项测试（45.8 秒），包含断线、取消、重放、工具失败、文件隔离、Markdown 安全渲染及真实归档 GLB 的旋转和爆炸预览。[浏览器验收](testing/BROWSER.md)

官方字标取自丰尚网站，登录主图按提供的门户参考重新制作。[字标来源](https://www.famsun.com/uploads/upload/images/20260728/bc6496aac4a5cdb5da5c90c494d9c2b7.svg)

测试主机仅提供本机 HTTP 入口，rootless Docker 缺少 cgroup 委派。测试开启了受限主机例外。真实生产机的 HTTPS 入口、CPU/内存/PID 限制、存储配额及备份恢复仍需在目标环境验收。[运行手册](OPERATIONS.md) · [本次部署预检](testing/workspace-preflight.json)

在开启注册的测试部署上，可将以下验收结果写到新的证据目录。浏览器任务另需安装 Playwright Chromium，或通过 `FAMSUN_E2E_CHROMIUM` 指定已安装的 Chromium。

```sh
uv run --locked --group eval python -m evals.workspace_smoke \
  --base-url http://127.0.0.1:8080 --output var/evidence/current/general.json
(cd web && node scripts/live-timeline.mjs http://127.0.0.1:8080 ../var/evidence/current)
```

## 2026 年 11 月 20 日 · 工程场景与隔离基线

测试日期为 2026 年 11 月 20 日。模型调用使用用户配置的 OpenAI 兼容服务 `qwen3.8-flash`，工作区运算在真实 Docker 容器中执行。最终 Compose 实例绑定测试主机的 `127.0.0.1:8080`。浏览器、FastAPI 控制面和官方文档 MCP 均参与了验收。

## 工程任务

使用同一组原始需求进行首轮测试和重新执行。**首轮五项中两项端到端通过**。评估器单独检查运行终态、事件树、下载请求、文件哈希和产物本身。在执行时限用完、模型接口超时或文件下载失败时，该次验收记为失败，即使磁盘上已有正确结果。**修复和恢复后，五类任务都交付了通过独立校验的产物。** 共保存 13 条记录，含 11 次独立任务、1 次同会话恢复及 1 次文件复验。每次尝试的明细、文件和模型生成的源码见 [工程任务记录](../evals/results/README.md)。

| 任务 | 独立验收方式 |
| --- | --- |
| 三体三维轨道 | 复算旋转初态与数值解。下载的 303 个状态最大偏差为 `4.2e-12`，相对能量漂移为 `2.77e-12`；已核对 CSV 与 1500 × 1050 PNG。[运行与产物](../evals/results/compose-three-body-files/three_body/result.json) |
| 环模制粒机 | 重新解析装配和爆炸 GLB。验证六个命名网格、环模中心通孔、双压辊的位置，以及 5 个零件相对机架的刚体位移。原运行超时，同会话追加一轮后正常结束，前后两个文件的哈希均一致。浏览器对实际文件执行旋转和爆炸操作。[两轮执行及文件](../evals/results/compose-pellet-recovery/pellet_mill/result.json) · [浏览器记录](testing/pellet-preview.json) |
| 轴承振动频谱 | 对照全部 2,049 个 FFT 频点和振幅，检测到 48.25、119.5、239 Hz 三个主峰，复算交流 RMS 和图像尺寸。[运行与产物](../evals/results/acceptance-01/vibration/result.json) |
| 四原料配方优化 | 独立枚举可行顶点，核对 1000 kg 质量约束、营养指标和成本，最优成本约为 265.04005 元/吨。发布版单轮返回答复并通过文件校验。[运行与产物](../evals/results/compose-feed/feed_mix/result.json) |
| 跨日停机归因 | 逐设备切分跨日区间、合并重叠事件，将七组归因明细与独立期望值对照，累计 80 设备分钟。[运行与产物](../evals/results/acceptance-01/downtime/result.json) |

另附一份项目制作者按假设尺寸建立的装配示例。它有 16 个命名零件组、138 个真实径向通孔及 42,056 个三角面，包含两份 GLB、渲染预览和可复现的 Blender 源码。上表的制粒机验收使用模型在会话内独立生成的六网格产物。[详细模型](../examples/pellet_press/README.md)

## 工作台和运行层

- **66 项 Python 回归通过。** 本地以 `FAMSUN_REQUIRE_DOCKER_TESTS=true` 运行，耗时 105.38 秒。覆盖账号隔离、相同账号的多个会话、幂等提交、并发准入、取消与再执行、SQLite 恢复、真实沙箱、路径和文件类型边界、模型工具错误、层级完整性及三种工程技能。GitHub CI 从干净环境执行依赖安装、测试和镜像构建。[CI](https://github.com/zlstart/codex_deepagents/actions/workflows/ci.yml)
- **常规浏览器套件 4 项，加实际制粒机文件重放 1 项，合计 5 项通过。** 常规回归通过真实 HTTP 与 SQLite 并使用可控执行器，便于重复覆盖断线、取消和异常；实物文件来自模型运行。桌面和手机布局、文件下载、CSV 表格、HTML 源码显示、隔离账户和无外部资源 GLB 均经验证。[浏览器验收](testing/BROWSER.md)
- **额外真实浏览器任务。** 从注册提交双半联轴器建模，最终从部署服务读回 30,668 字节、两个网格的 GLB。页面显示 77 个活动节点，35 个工具节点位于子智能体内。拖动旋转及设置 55% 爆炸幅度后，三次画布像素结果不同。复测包含 6 次工具失败，最终运行完成。一条异常探针的工具输出曾带入一次性 QA 会话令牌；该会话已主动注销，并验证旧 Cookie 访问账号接口返回 401。仓库不包含此令牌。首轮触发 900 秒时限。[首轮](testing/workbench-first-attempt.json) · [复测](testing/workbench-live.json) · [截图](testing/workbench-live.png)
- **MCP、多轮会话和记忆。** 两次真实双轮测试均通过。智能体经官方 MCP 检索并写入文档链接；下一轮找回仅保存在聊天里的校验码，读取原会话文件，下载与预期一致。另测跨 Docker 生命周期的私有偏好；两轮后只读规范哈希一致。[MCP 复验](testing/harness-recheck.json) · [Compose 两轮实测](testing/release-compose-mcp.json) · [会话记忆](testing/session-memory-live.json)
- **部署、权限与供应链。** Compose 重建服务后，原账号 Cookie 和 SQLite 会话仍可用。沙箱内执行过从 PyPI 联网安装、编译原生程序、CAD 布尔差和挤出，工具与环境卷跨轮持久化。服务预检验证模型调用、MCP、Docker 和 SQLite。[持久化验证](testing/rollout.json) · [预检](testing/deployment-preflight.json)

## 验收中修复的问题

| 现象 | 修复和复验 |
| --- | --- |
| VFS 下同时打开产物时 API 返回 503 | 原实现为每个只读请求启动约 796 MB 的工程镜像。改用约 49 MB 的只读辅助镜像，并修复控制命令超时的清理。对同一份轨道 CSV，单读从 10.89 秒降至 2.10 秒；两次列表加两次文件读取并发用时 3.99 秒，四个请求均成功。[原始浏览器失败](testing/browser-first-read.json) |
| 已生成文件后反复编辑、达到图步数或总时限 | 引入按验收目标收尾的指令、接近预算的可见提醒、可配置步数，以及通用 N 体、配方和装配技能脚本。保留硬性失败与恢复记录。最终增加禁用工具的预算收尾回合，用于保留已确认的结果和待办；触发强制收尾的运行仍标记为失败。独立真实模型短预算探针只调用一次模型，未执行工具、未写文件，答复正确说明未完成。[预算收尾证据](testing/budget-close-live.json) |
| 第三方模型生成长工具参数时连接超时 | 默认使用标准流式 Chat Completions，聚合工具参数后进入真实 LangChain 工具执行；兼容端点可关闭流式。 |
| 图中的失败或取消难以定位 | 记录非零命令退出、工具参数错误、未知工具与取消状态。修复取消后残留的未配对工具消息，并测试同一会话再次提交。 |
| 冷启动时并发初始化检查点可能出现 SQLite 锁竞争 | 只对初始化执行有界重试。任务副作用不会重放。 |
| HTTPS 反向代理和爆炸模型取景 | 按部署的外部 Origin 验证同源写入。三维预览按当前爆炸姿态的包围盒和视口宽高重设相机，已复验完整入镜。 |

## 生产适用条件

上线形态为 Linux 单节点、一个 API worker、专用 Docker daemon、SQLite 和持久卷。测试主机使用 rootless Docker 和 VFS，缺少 cgroup 委派。请求 1 GiB 内存与 128 个 PID 的探针实际读到 16 GiB 与无限 PID。功能验收使用了显式测试例外；**生产默认会拒绝在缺少 CPU、内存或 PID 控制器的主机启动任务**。

本次验证覆盖了单机部署与重启。目标生产机仍需实际验证 HTTPS 入口、资源和存储配额、备份恢复及业务并发规模。五项案例衡量了具体交付物；全自动运行的成功率需要用真实业务样本持续评估。模型为概念验证使用的尺寸和材料由生成者假设。[部署手册](OPERATIONS.md)

## 复现

```sh
uv sync --locked --group eval
FAMSUN_REQUIRE_DOCKER_TESTS=true uv run --locked --group eval pytest -q
(cd web && npm ci && npm run build && npx playwright install chromium && npm test)
uv run --locked --group eval python -m evals.live --output var/evidence/new-run
# MCP 专项验收使用显式开启 FAMSUN_MCP_ENABLED 的测试服务
uv run --locked --group eval python -m evals.harness_live --base-url http://127.0.0.1:8000 --output var/evidence/mcp.json
```
