# FAMSUN Agent 部署与验收

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
uv run --locked --group eval python -m evals.harness_live --base-url http://127.0.0.1:8000 --output var/evidence/mcp.json
```
