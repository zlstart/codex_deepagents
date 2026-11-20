# FAMSUN Agent 真实任务验收

通过部署服务的登录、会话、运行、事件和文件 HTTP 接口执行。数值与几何校验在独立的验收进程中读取下载文件完成。时间使用 UTC。

| 任务 | 验收 | Agent | 耗时（秒） | 失败步骤 |
|---|---|---|---:|---:|
| [三体三维轨道仿真](three_body/result.json) | 失败 | completed | 418.12 | 4 |

## 三体三维轨道仿真

运行 `75528b35d9014b3faa1d444286b8ae52`，开始于 2026-11-20T11:48:59+00:00。 [逐步轨迹](three_body/trace.json)

- 109 条生命周期事件，54 个节点，最大深度 3 层
- 验收失败：RuntimeError: GET /api/sessions/59134706ff0b4703a31f7b733a5bc34c/files returned 503: {"detail":"工作区暂不可用，请检查 Docker 服务和沙箱镜像"}
- 运行中失败步骤：tool / execute（序号 253，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 257，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 357，详见轨迹）
- 运行中失败步骤：tool / edit_file（序号 422，详见轨迹）
