# 如何阅读执行过程

任务开始后，主时间线显示模型已经公开说出的话。工具开始时会出现一行摘要，可以展开检查参数。结束后同一行更新状态和返回值。对于沙箱命令，返回的退出码也会显示。委派给子智能体的任务可以展开，里面有该子智能体自己的说明和工具。模型直接回答问题时，时间线可以只有正文。

这些内容来自 LangChain 的实际消息和工具回调。只发送工具请求的模型步骤会呈现工具活动。如果配置了非流式模型端点，正文会在该次模型调用返回时出现。旧版本保留的会话可以读回原有的活动摘要。工作区中的文件可以在右侧面板打开。

## 实现依据

- Codex 的消息分片带有线程、回合和消息 ID。命令记录分别保存输出和退出码，终端界面可以紧凑显示活动，再展开看上下文。本项目用稳定节点 ID 合并分片，用单调递增的序号恢复传输，用同一张卡片承载工具的开始与结束。[消息增量协议](https://github.com/openai/codex/blob/1145028ae47e8119955323d38ce34122d418f951/codex-rs/app-server-protocol/schema/typescript/v2/AgentMessageDeltaNotification.ts) · [活动数据结构](https://github.com/openai/codex/blob/1145028ae47e8119955323d38ce34122d418f951/codex-rs/app-server-protocol/schema/typescript/v2/ThreadItem.ts) · [命令渲染快照](https://github.com/openai/codex/blob/1145028ae47e8119955323d38ce34122d418f951/codex-rs/tui/src/exec_cell/snapshots/codex_tui__exec_cell__render__tests__truncated_live_output_preview_and_transcript.snap)
- Claude Agent SDK 将公开文本、工具请求、工具结果分别建模。结果标明调用 ID 和错误状态，子消息带有父调用 ID。本项目也单独渲染各类内容，并依据回调父子关系将并行子任务的活动归到各自分支。[Anthropic 官方 SDK 消息类型](https://github.com/anthropics/claude-agent-sdk-python/blob/3184360393a92ca8a6ec7bbbdee571afd1ce1c56/src/claude_agent_sdk/types.py)
- 开发期间通过官方 LangChain 文档 MCP 核对了公开文本分片、分段工具参数、已完成的工具消息，以及子智能体的来源识别。项目通过现有回调收集这些事件，再用 SSE 推送，历史重读使用相同的事件序号。运行普通用户任务时无需访问文档服务。[LangChain 流式输出](https://docs.langchain.com/oss/python/langchain/streaming#streaming-from-sub-agents) · [工具消息](https://docs.langchain.com/oss/python/langchain/messages#tool-message-5)
