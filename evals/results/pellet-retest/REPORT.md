# FAMSUN Agent 真实任务验收

通过部署服务的登录、会话、运行、事件和文件 HTTP 接口执行。数值与几何校验在独立的验收进程中读取下载文件完成。时间使用 UTC。

| 任务 | 验收 | Agent | 耗时（秒） | 失败步骤 |
|---|---|---|---:|---:|
| [环模制粒机装配与爆炸图](pellet_mill/result.json) | 失败 | failed | 1516.94 | 12 |

## 环模制粒机装配与爆炸图

运行 `44994326d24947719e9515f8eb8629ec`，开始于 2026-11-20T11:40:09+00:00。 [逐步轨迹](pellet_mill/trace.json)

- 验收失败：agent ended failed: 任务超过执行时限，已停止
- 验收失败：RuntimeError: GET /api/sessions/cb3ccd7d83824cb1bbc47e35df56d5b1/files returned 404: {"detail":"未找到文件"}
- 运行中失败步骤：tool / execute（序号 58，详见轨迹）
- 运行中失败步骤：tool / execute（序号 62，详见轨迹）
- 运行中失败步骤：tool / execute（序号 66，详见轨迹）
- 运行中失败步骤：tool / execute（序号 74，详见轨迹）
- 运行中失败步骤：tool / execute（序号 82，详见轨迹）
- 运行中失败步骤：tool / execute（序号 102，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 210，详见轨迹）
- 运行中失败步骤：tool / execute（序号 222，详见轨迹）
- 运行中失败步骤：tool / execute（序号 234，详见轨迹）
- 运行中失败步骤：tool / execute（序号 270，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 278，详见轨迹）
- 运行中失败步骤：run / 执行任务（序号 367，详见轨迹）
