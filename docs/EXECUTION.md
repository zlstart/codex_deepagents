# 如何阅读执行过程

点击每个回合的“执行过程”可切换简洁视图与完整时间线。新任务执行时默认展开，首次打开已经结束的历史回合默认收起。手动选择按标签页、会话和回合保存，刷新后仍生效。

- **收起时**，进行中的任务保留当前活动和状态。结束后仅保留展开入口、回合状态和已保存的最终答复。工具计数、失败次数和工具间发言可在展开后查看。运行失败或被取消时，可以展开检查先前产生的文字与工具结果。
- **展开时**，模型的公开回复随生成逐段出现。工具调用开始后即可查看参数，结束后同一行更新结果、错误和适用的退出码。只发起工具调用的步骤显示相应工具。非流式模型在该次模型调用返回时显示正文。
- **查看委派任务时**，展开子智能体以查看它自己的公开回复和工具调用。委派说明和返回值可以单独展开。并行执行的多个分支分别保留各自的父子关系。
- **刷新或重新连接后**，工作台按已保存的事件序号补读。本地保存的最终答复只展示一次。较早版本的运行可回看当时记录的活动摘要。

进行中的任务可从输入区停止。预算耗尽时，系统会尽可能保存已核验的部分结果和未完成事项，运行状态显示为失败。详情见[实时执行截图](screenshots/live-trace-running.webp)和[子智能体回放](screenshots/live-trace.webp)。

## 查看工作区文件

点击“工作区文件”或回复中的工作区文件链接可打开预览。左上角的“全部文件”返回清单，下载按钮获取原文件。每个用户只能读取自己的会话文件。

| 文件类型 | 工作台中的查看方式 |
| --- | --- |
| PDF、Word（DOC、DOCX、ODT、RTF）、演示文稿（PPT、PPTX、ODP） | 翻页、跳页、缩放或查看本页可提取的文字。Office 文档由断网的一次性容器转换，并由浏览器渲染分页版式。 |
| Excel（XLS、XLSX）、ODS | Excel 可以选择工作表，按每组 80 行、前 48 列查看数据，也可以切换打印版式。ODS 提供分页版式。 |
| HTML、Markdown | HTML 可在静态页面和源码之间切换。预览允许内联样式和支持的内联图片，脚本、外部请求和表单提交在预览中被禁用。Markdown 可在排版后的文档与原文之间切换。 |
| 图片、CSV、文本、代码 | 图片直接显示，CSV 以表格展示，文本与代码保留原有换行。SVG 以源码查看。 |
| 自包含 GLB | 使用交互式三维视图旋转、缩放模型。装配和爆炸视图分别生成文件时，可从清单切换查看。[制粒机示例](screenshots/pellet-preview.webp) |

文本、Markdown 和 HTML 预览限 512 KiB，CSV 预览限 8 MiB，其他受支持文件的下载上限为 32 MiB。预览失败时可以重试，或在允许的下载范围内获取原件。工作区文件预览使用会话授权，转换容器从标准输入读取本次文件快照。

## 删除会话

在左侧会话列表点击删除按钮，核对对话框中的会话名称后选择“确认删除”。默认焦点在“取消”，也可以按 Esc 退出。会话内还有排队或运行中的任务时，须先停止任务或等待结束。

删除成功后，会话记录、运行事件、LangGraph 检查点、预览缓存、工作区和 Python 环境卷一并清理。删除当前会话后工作台切换到相邻会话。删除其他会话时当前草稿可继续编辑。部分清理操作失败时，后端先封闭该会话，再在后台重试。启用 Langfuse 的部署应通过其项目权限与保留策略管理已导出的记录。[删除确认界面](screenshots/session-delete.webp)

## 核对 Langfuse 记录

运行仓库的本地 Langfuse 后，在工作台提交一个新任务并等待结束。展开“执行过程”，在底部复制“任务编号”。管理员如需使用校验脚本，可从浏览器开发者工具的网络面板中读取 `POST /api/sessions/<session>/runs` 响应的 `session_id`，以及 `GET /api/auth/me` 返回的用户 `id`。从仓库根目录运行以下只读校验，以实际值替换三个占位符。

```sh
uv run --locked --env-file deploy/langfuse/.env python -m deploy.langfuse.verify_trace \
  --user USER_ID --session SESSION_ID --run RUN_ID
```

脚本等待本地 Langfuse 完成摄取，核对任务、用户和会话标识，并输出 Trace ID、观测数量与类型。可在 Langfuse 项目中继续展开该 Trace。若只需任务对应的 Trace ID，可直接在 FAMSUN 服务中运行 `docker compose exec -T app python -m famsun.telemetry RUN_ID`。配置、自托管版本要求与内容导出选项见[Langfuse 指南](LANGFUSE.md)。

## 实现依据

- Codex 的消息分片带有线程、回合和消息 ID。命令记录分别保存输出和退出码，终端界面可以紧凑显示活动，再展开看上下文。本项目用稳定节点 ID 合并分片，用单调递增的序号恢复传输，用同一张卡片承载工具的开始与结束。[消息增量协议](https://github.com/openai/codex/blob/1145028ae47e8119955323d38ce34122d418f951/codex-rs/app-server-protocol/schema/typescript/v2/AgentMessageDeltaNotification.ts) · [活动数据结构](https://github.com/openai/codex/blob/1145028ae47e8119955323d38ce34122d418f951/codex-rs/app-server-protocol/schema/typescript/v2/ThreadItem.ts) · [命令渲染快照](https://github.com/openai/codex/blob/1145028ae47e8119955323d38ce34122d418f951/codex-rs/tui/src/exec_cell/snapshots/codex_tui__exec_cell__render__tests__truncated_live_output_preview_and_transcript.snap)
- Claude Agent SDK 将公开文本、工具请求、工具结果分别建模。结果标明调用 ID 和错误状态，子消息带有父调用 ID。本项目也单独渲染各类内容，并依据回调父子关系将并行子任务的活动归到各自分支。[Anthropic 官方 SDK 消息类型](https://github.com/anthropics/claude-agent-sdk-python/blob/3184360393a92ca8a6ec7bbbdee571afd1ce1c56/src/claude_agent_sdk/types.py)
- 开发期间通过官方 LangChain 文档 MCP 核对了公开文本分片、分段工具参数、已完成的工具消息，以及子智能体的来源识别。项目通过现有回调收集这些事件，再用 SSE 推送，历史重读使用相同的事件序号。运行普通用户任务时无需访问文档服务。[LangChain 流式输出](https://docs.langchain.com/oss/python/langchain/streaming#streaming-from-sub-agents) · [工具消息](https://docs.langchain.com/oss/python/langchain/messages#tool-message-5)
