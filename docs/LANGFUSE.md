# Langfuse 运行追踪

FAMSUN 使用 Langfuse Python SDK 5.2.0 的官方 LangChain CallbackHandler。每次任务在 Langfuse 中对应一条 trace，可以展开模型调用、工具、子智能体及其内部调用。工具的非零退出码、文件读取失败、任务失败及取消会保留对应状态。模型供应商返回 token 计数时，generation 同时记录用量。工作台的实时执行记录由本地 SQLite 独立保存。[官方 LangChain 集成](https://langfuse.com/integrations/frameworks/langchain)

本机从零安装、登录、备份和恢复见[本地 Docker Compose 部署手册](../deploy/langfuse/README.md)。

## 连接部署

该 SDK 要求自托管 Langfuse **4.59.0 及以上**。仓库的本地部署固定使用 4.83.0。 [官方版本要求](https://langfuse.com/docs/observability/sdk/upgrade-path/python-v4-to-v5#minimum-versions)

在 Langfuse 项目的设置中创建项目 API keys，将以下变量注入 FAMSUN 的 app 服务，然后重新创建该容器。自托管时 `LANGFUSE_BASE_URL` 填写 **app 容器可访问的 Langfuse Web 服务根地址**。下面的示例使用两者共享的 Docker 网络及服务名。域名部署使用 HTTPS。外部 Langfuse 项目同样需要管理员明确配置其地址和凭据。[官方自托管文档](https://langfuse.com/self-hosting)

```dotenv
FAMSUN_LANGFUSE_ENABLED=true
LANGFUSE_BASE_URL=http://langfuse-web:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
FAMSUN_LANGFUSE_CAPTURE_CONTENT=false
```

控制面向 `/api/public/otel/v1/traces` 发送经过项目认证的 OTLP/HTTP 请求，SDK 携带 `x-langfuse-ingestion-version: 4`。Langfuse 项目密钥只保存在服务端。兼容模型端点支持 OpenAI 的 `stream_options.include_usage` 时，可设置 `FAMSUN_LLM_STREAM_USAGE=true` 获取流式响应的实际 token 数。留空采用 LangChain 的端点默认行为。

## 跟踪与数据访问

在工作台展开“执行过程”，在底部复制任务编号。管理员可以在运行该任务的 FAMSUN 服务中执行以下只读查询，再在 Langfuse 项目按 Trace ID 搜索，也可通过项目的 `GET /api/public/v2/observations?traceId=<traceId>` 检索。

```bash
docker compose exec -T app /app/.venv/bin/python -m famsun.telemetry YOUR_RUN_ID
```

默认部署关闭 Langfuse 导出。开启后，默认可见运行结构、耗时、模型名、用量及状态。用户、会话与任务 ID 分别使用项目密钥进行 HMAC 派生，单个会话的多次运行可以关联。轮换密钥后，后续运行的派生标识也随之变化。保留旧项目已经导出的记录时，应按 Langfuse 项目权限与保留策略管理。工作台删除会话只处理本地资源。

`FAMSUN_LANGFUSE_CAPTURE_CONTENT=true` 额外导出有长度上限的任务提问、最终答复、工具参数与成功结果预览。预览会过滤常见凭据、已配置密钥、隐藏思考块、媒体和框架私有字段。可见文档正文、路径、业务数据和普通个人信息仍可能进入项目。先确认项目访问权限与业务授权，再启用该选项。模型对话历史与检查点由 FAMSUN 自行管理。[Langfuse 数据遮蔽](https://langfuse.com/docs/observability/features/masking)

SDK 独占一个 OpenTelemetry provider，按任务结束信号在后台合并刷新。服务正常退出会等待 SDK 完成刷新与关闭。观察端不可用、鉴权错误或导出队列耗尽时，任务可继续执行，对应远端记录可能不完整。排障时检查 app 日志、项目密钥、服务间网络和 Langfuse 后台摄取状态，本地执行事件仍可供核对。
