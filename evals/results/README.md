# FAMSUN Agent 工程验收记录

首轮和独立重跑均在空白会话中提交相同的用户需求，使用真实模型、LangChain Deep Agents 和 Docker。文件复验只重新读取原运行的事件和产物。同一会话恢复会提交一次后续请求。每次尝试均保留独立记录。

共 13 条验收记录，包括 11 次独立任务运行、1 次同会话跟进，以及 1 次原运行文件复验。最终可复验的交付如下。

- 三体轨迹的最大状态误差为 4.2×10⁻¹²，相对能量漂移为 2.77×10⁻¹²。模型单轮完成，首次读取文件返回 503，修复文件服务后同一运行的产物经 HTTP 完整下载并通过校验。[轨迹图](compose-three-body-files/three_body/orbits.png) · [数据](compose-three-body-files/three_body/trajectory.csv) · [源码与参数](compose-three-body-files/three_body/generated_sources.zip)
- 环模制粒机在 900 秒时限内生成了两份 GLB，原运行超时。同一会话跟进获得最终答复，前后文件哈希一致。每份场景含六个命名零件，爆炸图中五个零件相对机架移开，几何与姿态保持一致。[装配模型](compose-pellet-recovery/pellet_mill/pellet.glb) · [爆炸模型](compose-pellet-recovery/pellet_mill/exploded.glb) · [生成清单](compose-pellet-recovery/pellet_mill/generated_sources.zip) · [浏览器实测](../../docs/testing/BROWSER.md)
- 振动任务校验了 2,049 个频点和交流均方根，三个主峰为 48.25、119.5、239 Hz。[频谱图](acceptance-01/vibration/spectrum.png)
- 一吨饲料配方满足四原料用量及营养约束，最低成本为 265.04005 元/吨。[配方数据](compose-feed/feed_mix/formula.json)
- 对重叠停机时间按优先级归因后，两台机器跨日共停机 80 分钟，拆成七组正时长记录。[归因数据](acceptance-01/downtime/downtime.csv)

| 任务 | 首轮 | 复测 | Compose 单轮 | 原会话恢复或文件复验 |
|---|---|---|---|---|
| 三体三维轨道仿真 | [未通过](acceptance-01/three_body/result.json) | [未通过](three-body-retest/three_body/result.json) | [未通过](compose-three-body/three_body/result.json) | [通过](compose-three-body-files/three_body/result.json) |
| 环模制粒机装配与爆炸图 | [未通过](acceptance-01/pellet_mill/result.json) | [未通过](pellet-retest/pellet_mill/result.json) | [未通过](compose-pellet/pellet_mill/result.json) | [通过](compose-pellet-recovery/pellet_mill/result.json) |
| 轴承振动频谱诊断 | [通过](acceptance-01/vibration/result.json) | — | — | — |
| 四原料饲料配方优化 | [未通过](acceptance-01/feed_mix/result.json) | [未通过](feed-retest/feed_mix/result.json) | [通过](compose-feed/feed_mix/result.json) | — |
| 跨日停机去重与归因 | [通过](acceptance-01/downtime/result.json) | — | — | — |

通过要求 Agent 完成最终答复、工具与模型事件可追溯、产物下载成功并通过独立数值或几何检验。中间出现错误而自行恢复的情况会保留在轨迹中。首轮三体与配方在终态失败后保留了工作卷，通过只读方式取回的产物另作验算。

## 运行环境与报告

- [首轮部署](acceptance-01/REPORT.md)。前台 HTTP 服务，MCP 开启。初始图步数上限 100，总执行时限 900 秒。
- [三体第二次](three-body-retest/REPORT.md)。独立 HTTP 服务与新会话，使用相同需求，调整了结束任务的指令和图步预算。
- [制粒机第二次](pellet-retest/REPORT.md)。新会话，加入 CAD 依赖、明确委派规则，服务时限 1,500 秒。
- [配方第二次](feed-retest/REPORT.md)。新会话，服务时限 1,500 秒。
- [Compose 三体](compose-three-body/REPORT.md)。发布配置运行，新会话，科学仿真技能提供通用三体积分与可视化脚本。
- [Compose 制粒机](compose-pellet/REPORT.md)。发布配置运行，新会话，机械技能支持由模型设计的部件与位移清单。
- [Compose 配方](compose-feed/REPORT.md)。发布配置运行，新会话，配方使用通用线性规划技能。
- [三体原运行文件复验](compose-three-body-files/REPORT.md)。复用已完成的同一 run，通过独立轻量 Docker 文件服务复验普通用户的文件列表、下载及产物质量，保留原始 503 证据。
- [制粒机原会话恢复](compose-pellet-recovery/REPORT.md)。先下载并独立验证原始 GLB，再在同一个会话跟进，检查原始与后续运行的完整轨迹，并逐字节比较两份交付文件。

验收器和任务原文见 [运行说明](../README.md)。SHA-256 与各任务的节点统计见对应的 result.json。生成脚本和输入压缩包直接收录模型在测试工作区写出的 Python 或 JSON 文件。数据来源均为任务内给定的合成输入。
