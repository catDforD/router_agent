## 当前后端实现思路梳理

### 当前 agent loop 工具集

Main Agent 当前暴露的是一组 Codex-like 通用工具：

- `list_files` / `read_file` / `write_file` / `apply_patch`
- `exec_command` / `git_status`
- `read_artifact` / `write_artifact`
- `call_mcp_tool`

`finish_task` 已删除，不再作为工具或兼容入口存在。任务终止由 runtime lifecycle 控制。

### 当前 agent loop 链路

当前链路是：

1. 用户创建 session 或在 session 内追加 message。
2. 后端为这次输入创建一个新的 run/task。
3. 默认直接进入 Orchestration Chat Completions tool loop。
4. 模型可输出公开进展、调用工具、读取/修改文件、执行命令或调用 MCP。
5. 当模型返回“只有自然语言、没有 tool call”时，runtime 视为请求停止。
6. StopPolicy 通过后，runtime 写 `agent.final_response`、`agent.completed` 和 run terminal event。

也就是说，模型负责执行和回答，runtime 负责最终状态落库。
当前 `MAIN_AGENT_PROVIDER` 只支持 `openai_compatible`，不再保留旧 provider 或独立 Intake 分类路径。新任务仍以 `created/intake/unknown/L0` 创建，但默认 Main Agent 直接进入 tool-loop orchestration；需要调度 PLC/domain worker 时由工具路径准备任务上下文。

### 当前系统提示词是什么？

默认链路只使用 Orchestration 提示词：

- 把 Main Agent 定义为 Codex-like 执行代理。
- 要求它探索 workspace、读写文件、运行命令、记录 artifact、可选调用 MCP/domain tools。
- 完成时直接给自然最终回答。

提示词明确要求不展示 hidden reasoning，不输出链式思考，只展示公开进展、执行结果、假设、验证和阻塞点。
旧 Intake 提示词和 standalone structured Intake runner 已删除。

### 后端提供的 SSE 接口目前输出是哪些信息？

主要 SSE 信息包括：

- task/run 生命周期：`task.created`、`task.updated`、`task.succeeded/failed/cancelled`
- agent 生命周期：`agent.started`、`agent.turn_started`、`agent.message`、`agent.final_response`、`agent.completed`
- 工具过程：`agent.tool_called`、`agent.tool_result`
- stop 控制：`agent.stop_blocked`
- artifact / worker / gate 事件：例如 `artifact.created`、`worker.completed`、`gate.passed/failed`

前端消费的是 session SSE：`GET /api/sessions/{session_id}/events`。单次 run 结束后 SSE 不应永久关闭，后续 message 会继续推送新的 run 事件。

### 当前 agent loop 是怎么做的会话管理？

现在采用 Claude Code-like 的 session/run 分层：

- `AgentSession` 表示可持续对话，绑定 workspace、上下文、最近 run 和事件序列。
- `AgentRun` 表示一次用户输入触发的执行；一个 session 可以有多个 run。
- 每次追问都会在同一 session 下创建新的 run/task，而不是复用已 terminal 的 task。
- 下一轮模型输入会带上 bounded recent transcript：最近几轮 user message、final response、artifact 线索和 workspace 状态。
- session 只有显式 archive/cancel 才结束；单次 run 成功只结束该 run。
