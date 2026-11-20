# FAMSUN Agent 验收报告

本次验收使用用户配置的第三方 Chat Completions 服务、真实 LangChain 文档 MCP 和 Docker Engine 29.9.2。工作区含可重跑的独立数值、几何和事件验证器。模型产物与每次尝试的结果分别归档。

## 已验证的基础能力

- 两次运行双轮 MCP 测试。第一轮通过官方 MCP 查找文档并将 URL 写进会话文件；第二轮从之前的对话中恢复随机值，并读取同一工作区的文件。最终复测的 16 个活动节点全部结束。见 [初测](testing/harness-live.json) 和 [复测](testing/harness-recheck.json)。
- Docker 沙箱验证覆盖两个用户、同一用户的两个会话、包和文件跨次执行持久化、容器取消与清理、无凭据环境、路径边界和内网访问限制。
- 浏览器自动化通过真实 HTTP 与 SQLite 覆盖登录、注册、恢复会话、层级展开、取消、文件下载、GLB 旋转与爆炸、移动端菜单和异常反馈。此套件使用可控 runner，真实推理结果单独验收。
- 测试主机的 rootless Docker 缺少 cgroup 委派。严格生产预检应拒绝在这台主机运行多租户任务；本次功能验收使用了测试例外。生产需在目标主机验证 CPU、内存、PID 和磁盘配额。

## 复现

```sh
uv sync --locked --group eval
FAMSUN_REQUIRE_DOCKER_TESTS=true uv run --locked --group eval pytest -q
(cd web && npm ci && npm run build && npx playwright install chromium && npm test)
uv run --locked --group eval python -m evals.live --output var/evidence/new-run
uv run --locked --group eval python -m evals.harness_live --base-url http://127.0.0.1:8000 --output var/evidence/mcp.json
```
