# FAMSUN Agent 真实任务验收

通过部署服务的登录、会话、运行、事件和文件 HTTP 接口执行。数值与几何校验在独立的验收进程中读取下载文件完成。时间使用 UTC。

| 任务 | 验收 | Agent | 耗时（秒） | 失败步骤 |
|---|---|---|---:|---:|
| [三体三维轨道仿真](three_body/result.json) | 通过 | completed | 29.53 | 4 |

## 三体三维轨道仿真

运行 `75528b35d9014b3faa1d444286b8ae52`，开始于 2026-11-20T12:06:03+00:00。 [逐步轨迹](three_body/trace.json)

- 只读复验同一已结束的运行。原验收耗时 418.12 秒，此处耗时为复验所需时间。
- 109 条生命周期事件，54 个节点，最大深度 3 层
- 最大质心位移 2.32e-16，总动量模长峰值 6.89e-16
- 303 个三维状态，最大状态误差 4.2e-12，最大相对能量漂移 2.77e-12
- 1500×1050 PNG
- 运行中失败步骤：tool / execute（序号 253，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 257，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 357，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 422，详见轨迹）
- [智能体最终答复](three_body/answer.txt)
- [生成脚本和输入](three_body/generated_sources.zip) · checks.json、crosscheck_independent.py、initial.json、plot_orbits.py、prepare_initial.py、verify.py、verify_report.json
- [trajectory.csv](three_body/trajectory.csv) · 39111 bytes
- [orbits.png](three_body/orbits.png) · 199111 bytes
