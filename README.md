# FAMSUN Agent

丰尚的通用智能体工作台。你可以直接提问，也可以交给它一项需要查文件、运行程序或协作完成的任务。在同一个会话里读到智能体的实时回复、展开工具调用、查看子智能体的工作，并预览或下载交付文件。

![FAMSUN Agent 工作台](docs/screenshots/workspace.webp)

[登录页](docs/screenshots/login.webp) · [运行中的工具参数和结果](docs/screenshots/live-trace-running.webp) · [展开子智能体](docs/screenshots/live-trace.webp) · [执行视图与参考实现](docs/EXECUTION.md)

## 部署

适用于一台 Linux 主机、一个 API 进程和一个专用 Docker Engine 28+。安装 Docker Compose v2，启用 cgroup v2 的 CPU、内存和进程控制器。模型服务需支持多轮工具调用。推理服务可以部署在其他机器。

1. 将模型配置注入运行 Compose 的 shell，或复制 `.env.example` 到仅管理员可读的 `.env`。填写 `LLM_BASE_URL`、`LLM_MODEL` 和 `LLM_API_KEY`。地址可以是 API 根路径，也可以包含末尾的 `/chat/completions` 或 `/responses`。配置 `DOCKER_GID` 为 Docker socket 在控制面容器内的组 ID，通常可通过 `stat -Lc %g /var/run/docker.sock` 获得。
2. 设置 `FAMSUN_PUBLIC_ORIGIN` 为用户浏览器访问的 HTTPS 源，包括非默认端口。将 TLS 反向代理指向 `127.0.0.1:8000`。本仓库的 Compose 默认仅监听回环，启用 Secure Cookie，并关闭公开注册。
3. 在仓库根目录构建服务端、沙箱和轻量文件读取/网络代理镜像，启动服务，配置首个账号：

```sh
docker compose --profile build build sandbox-image helper-image app
docker compose up -d --wait app
docker compose exec app python -m famsun.manage create-user engineer
docker compose exec app python -m famsun.doctor --strict --live-model
```

`doctor` 检查 SQLite、Docker 和沙箱镜像。开启文档集成时也检查 MCP。加上 `--live-model` 会产生一次实际的含工具回传的模型调用。监控可轮询 `GET /api/health`。专用 Docker socket 仅挂在服务端容器，生产部署细节见 [运行手册](docs/OPERATIONS.md)。

**本机 HTTP 体验**可将 `FAMSUN_PUBLIC_ORIGIN` 配为 `http://127.0.0.1:8000`，并设置 `FAMSUN_SECURE_COOKIES=false`。如需通过页面创建账户，再设置 `FAMSUN_ALLOW_SIGNUP=true`。容器配置变化后用 `docker compose up -d app` 更新。

## 使用

- 登录后新建会话，直接输入需求。需要交付文件时，可以指定文件名、格式及验收方式。
- 时间线按执行顺序呈现公开的模型回复、工具调用和委派任务。点开工具查看参数、结果和适用的退出码，点开子智能体继续查看内部步骤。进行中的任务可以停止，刷新后可接着阅读。
- 工作区面板支持图片、文本、Markdown、CSV 和 GLB 预览，以及文件下载。每个用户的每个会话有独立的 Docker 工作区和 Python 安装环境，后续对话可以继续使用。
- 技能按 Deep Agents 的 `SKILL.md` 格式组织，新增部署技能后重建沙箱镜像。[内置技能](famsun/skills)
- 官方 LangChain 文档查询默认关闭。设置 `FAMSUN_MCP_ENABLED=true` 后，模型可在遇到相关 API 问题时选择查询，实际调用时才连接 `https://docs.langchain.com/mcp`。

## 架构

```mermaid
flowchart LR
    Browser[浏览器工作台] -->|认证 / 增量事件 / 文件| API[FastAPI]
    API --> DB[(SQLite WAL)]
    API --> Graph[Deep Agents + LangGraph]
    Graph --> Checkpoint[(SQLite 检查点)]
    Graph --> Model[兼容模型]
    Graph -. 按需查询 .-> Docs[LangChain 文档 MCP]
    Graph --> Specialists[子智能体]
    Graph --> Box[每会话 Docker 沙箱]
    Specialists --> Box
    Box --> Vol[工作区卷 + Python 环境卷]
    Box --> Proxy[会话独立的公网 HTTP/S 出口]
```

运行层实现见 [harness.py](famsun/harness.py)，活动树映射见 [trace.py](famsun/trace.py)。采用官方的 `create_deep_agent`、`BaseSandbox`、`AsyncSqliteSaver` 和 `MultiServerMCPClient`。锁文件固定了此次构建的依赖版本。上游接口参见 [Deep Agents 文档](https://docs.langchain.com/oss/python/deepagents/overview)、[沙箱后端](https://docs.langchain.com/oss/python/deepagents/sandboxes) 和 [MCP 适配器](https://github.com/langchain-ai/langchain-mcp-adapters)。

## 开发与验证

```sh
uv sync --locked --group eval
(cd web && npm ci && npm run build)
docker build -f deploy/helper.Dockerfile -t famsun-helper:local .
docker build -f deploy/sandbox.Dockerfile -t famsun-sandbox:local .
uv run --locked --group eval pytest -q
FAMSUN_ALLOW_SIGNUP=true FAMSUN_SECURE_COOKIES=false \
  uv run --locked uvicorn famsun.api:app --host 127.0.0.1 --port 8000
```

五项真实验收经由产品的 HTTP 接口完成。评估器读取每个运行的父子事件，下载产物并独立验证其数值或几何。可提供专用的 `FAMSUN_EVAL_USERNAME`、`FAMSUN_EVAL_PASSWORD`；只在允许注册的测试部署上自动创建验收账号。每次指定新的证据目录。

```sh
uv run --locked --group eval python -m evals.live \
  --base-url http://127.0.0.1:8000 --output var/evidence/release-01 --parallel 2
```

浏览器、模型和 Docker 的实测范围见[验收报告](docs/TEST_REPORT.md)。工程场景另有五项独立校验，包含三体轨道和制粒机爆炸图。[工程验收记录与产物](evals/results/README.md)
