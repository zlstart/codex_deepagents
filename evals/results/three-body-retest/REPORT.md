# FAMSUN Agent 真实任务验收

通过部署服务的登录、会话、运行、事件和文件 HTTP 接口执行。数值与几何校验在独立的验收进程中读取下载文件完成。时间使用 UTC。

| 任务 | 验收 | Agent | 耗时（秒） | 失败步骤 |
|---|---|---|---:|---:|
| [三体三维轨道仿真](three_body/result.json) | 失败 | failed | 798.44 | 4 |

## 三体三维轨道仿真

运行 `b5b862affa14424cad79212249b90843`，开始于 2026-11-20T11:35:33+00:00。 [逐步轨迹](three_body/trace.json)

- 最大质心位移 5.77e-13，总动量模长峰值 1.73e-12
- 303 个三维状态，最大状态误差 4.12e-12，最大相对能量漂移 3.63e-12
- 1500×1050 PNG
- 验收失败：agent ended failed: 任务已达到最大执行步数，请缩小任务范围或在本会话继续
- 运行中失败步骤：tool / edit_file（序号 47，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 95，详见轨迹）
- 运行中失败步骤：agent / FAMSUN Agent（序号 216，详见轨迹）
- 运行中失败步骤：run / 执行任务（序号 217，详见轨迹）
- [生成代码](three_body/generated_sources.zip) · three_body.py、verify_artifacts.py
- [trajectory.csv](three_body/trajectory.csv) · 30315 bytes
- [orbits.png](three_body/orbits.png) · 234435 bytes
