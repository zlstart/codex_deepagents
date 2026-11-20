# FAMSUN Agent 真实任务验收

通过部署服务的登录、会话、运行、事件和文件 HTTP 接口执行。数值与几何校验在独立的验收进程中读取下载文件完成。时间使用 UTC。

| 任务 | 验收 | Agent | 耗时（秒） | 失败步骤 |
|---|---|---|---:|---:|
| [三体三维轨道仿真](three_body/result.json) | 失败 | failed | 516.54 | 5 |
| [环模制粒机装配与爆炸图](pellet_mill/result.json) | 失败 | failed | 743.64 | 3 |
| [轴承振动频谱诊断](vibration/result.json) | 通过 | completed | 226.27 | 0 |
| [四原料饲料配方优化](feed_mix/result.json) | 失败 | failed | 906.39 | 5 |
| [跨日停机去重与归因](downtime/result.json) | 通过 | completed | 287.36 | 1 |

## 三体三维轨道仿真

运行 `069abc54db794c7fb1d6a8b73dbaac23`，开始于 2026-11-20T11:25:56+00:00。 [逐步轨迹](three_body/trace.json)

- 验收失败：InvalidArtifact: agent ended failed: 任务已达到最大执行步数，请缩小任务范围或在本会话继续
- 此次运行已失败。结束后使用只读产物服务复查了该测试会话的工作卷。
- 失败后复算：最大质心位移 1.7e-12，总动量模长峰值 7.03e-12
- 失败后复算：303 个三维状态，最大状态误差 7.85e-12，最大相对能量漂移 8.15e-12
- 失败后复算：2600×1440 PNG
- 运行中失败步骤：tool / execute（序号 69，详见轨迹）
- 运行中失败步骤：tool / execute（序号 106，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 170，详见轨迹）
- 运行中失败步骤：agent / FAMSUN Agent（序号 213，详见轨迹）
- 运行中失败步骤：run / 执行任务（序号 214，详见轨迹）
- [生成脚本和输入](three_body/generated_sources.zip) · three_body.py
- [trajectory.csv](three_body/trajectory.csv) · 30539 bytes
- [orbits.png](three_body/orbits.png) · 322012 bytes

## 环模制粒机装配与爆炸图

运行 `03c52897bb894c6fb3c9f6d329072841`，开始于 2026-11-20T11:25:56+00:00。 [逐步轨迹](pellet_mill/trace.json)

- 验收失败：InvalidArtifact: agent ended failed: 模型连接失败，请检查模型端点与网络后重试
- 此次运行已失败。结束后使用只读产物服务复查了该测试会话的工作卷。
- 运行中失败步骤：model / 模型响应（序号 303，详见轨迹）
- 运行中失败步骤：agent / FAMSUN Agent（序号 304，详见轨迹）
- 运行中失败步骤：run / 执行任务（序号 305，详见轨迹）

## 轴承振动频谱诊断

运行 `9db4ea94da404a9f84b4c413c7b50bf4`，开始于 2026-11-20T11:25:56+00:00。 [逐步轨迹](vibration/trace.json)

- 51 条生命周期事件，25 个节点，最大深度 3 层
- 2,049 个单边 FFT 频点及振幅、前三个峰值频率和交流均方根通过独立校验
- 1800×920 PNG
- [智能体最终答复](vibration/answer.txt)
- [生成脚本和输入](vibration/generated_sources.zip) · accel_spectrum.py、verify_outputs.py
- [spectrum.csv](vibration/spectrum.csv) · 65151 bytes
- [vibration.json](vibration/vibration.json) · 713 bytes
- [spectrum.png](vibration/spectrum.png) · 100572 bytes

## 四原料饲料配方优化

运行 `c4bebbf848604e55afa2accd25e01650`，开始于 2026-11-20T11:29:42+00:00。 [逐步轨迹](feed_mix/trace.json)

- 验收失败：InvalidArtifact: agent ended failed: 任务超过执行时限，已停止
- 此次运行已失败。结束后使用只读产物服务复查了该测试会话的工作卷。
- 失败后复算：总质量 1 吨，各原料和营养成分满足约束，最优成本为每吨 265.04005 元
- 运行中失败步骤：tool / execute（序号 339，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 371，详见轨迹）
- 运行中失败步骤：tool / execute（序号 375，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 387，详见轨迹）
- 运行中失败步骤：run / 执行任务（序号 393，详见轨迹）
- [生成脚本和输入](feed_mix/generated_sources.zip) · lp_feed.py、sensitivity.py、verify_formula.py
- [formula.json](feed_mix/formula.json) · 255 bytes

## 跨日停机去重与归因

运行 `b9b09292b4da4b55b79a1d6f1ef81e4f`，开始于 2026-11-20T11:34:32+00:00。 [逐步轨迹](downtime/trace.json)

- 67 条生命周期事件，33 个节点，最大深度 3 层
- 跨日切分后得到七组归因记录，重复区间已消除，设备累计停机 80 分钟
- 运行中失败步骤：tool / execute（序号 243，详见轨迹）
- [智能体最终答复](downtime/answer.txt)
- [生成脚本和输入](downtime/generated_sources.zip) · downtime_report.py、verify_downtime.py
- [downtime.csv](downtime/downtime.csv) · 204 bytes
- [downtime.json](downtime/downtime.json) · 115 bytes
