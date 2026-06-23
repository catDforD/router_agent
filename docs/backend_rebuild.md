## 当前后端实现思路梳理

### 当前 agent loop 工具集

当前 Main Agent 已切到 Codex-like 通用工具集，核心工具是：

- Workspace 文件工具：`list_files`、`read_file`、`write_file`、`apply_patch`
- 本地执行工具：`exec_command`、`git_status`
- Artifact 工具：`read_artifact`、`write_artifact`
- Domain/MCP 工具：`call_mcp_tool`
- 终态工具：`finish_task`

其中 `call_mcp_tool` 负责把 PLC worker 降级为可选 domain tool，例如
`plc_dev.run`、`plc_test.run`、`plc_formal.run`、`plc_repair.run`。本地文件和命令工具需要显式开启
`AGENT_EXECUTION_MODE=local_full_access`。


### 当前 agent loop 链路

当前链路是通用 Chat Completions tool loop：

1. `/api/tasks` 创建任务，保存 `TaskState`、raw request artifact、workspace/project context。
2. Runtime 启动 Main Agent run，写入 `agent.started` 和 trace id。
3. Main Agent 用 system prompt + task context + tool schemas 发起多轮模型调用。
4. 模型按需探索 workspace、读写文件、执行命令、写 artifact，或通过 `call_mcp_tool` 调用 PLC domain worker。
5. 每次工具调用都会记录 `agent.tool_called` / `agent.tool_result`；worker 路径额外记录 `worker.started`、`artifact.created`、`worker.completed/error`。
6. Agent 最后调用 `finish_task` 写 final report / replay log，并把任务置为 `succeeded`、`partial_failed`、`failed` 或 `cancelled`。

现在不再强制旧的 `update_plan -> PLC worker -> quality_gate -> final_report -> finish_task`
固定流程；是否测试、修复、调用 worker 由 Agent 根据任务自行选择。

### 当前系统提示词是什么？

当前主 Agent 的 system prompt 由 `build_orchestration_instructions()` 生成，定位是：

> Router Main Agent 是一个超级 Agent ，在配置的 workspace 内读文件、改文件、跑命令、写 artifact、按需调用 MCP/domain tools，最后必须通过 `finish_task` 结束任务。

核心规则：

- 先理解任务和 workspace；缺上下文时用 `list_files`、`read_file`、`git_status`、`read_artifact`。
- 修改文件用 `write_file` 或 `apply_patch`，改已有文件优先 patch。
- 修改后尽量用 `exec_command` 跑可用的验证命令。
- 大输出、报告、长期证据写入 `write_artifact`。
- `call_mcp_tool` 只用于可配置的外部/domain tools；PLC worker 不是默认路径。
- 纯 assistant 文本不能结束任务，必须调用 `finish_task`。
- 对用户可见的进度要简洁，不暴露 hidden reasoning。


### 后端提供的 SSE 接口目前输出是哪些信息？

SSE 接口是：

```http
GET /api/tasks/{task_id}/events
```

返回 `text/event-stream`，只推送 `visibility=user` 的 `RouterEvent`。支持：

- `after_seq` query 参数续传
- `Last-Event-ID` header 续传
- 空闲时发送 `: keepalive`

每个 SSE frame 结构是：

```text
id: <event.seq>
event: <event.type>
data: <RouterEvent JSON>
```

当前主要事件类型包括：

- task：`task.created`、`task.updated`、`task.succeeded`、`task.partial_failed`、`task.failed`、`task.cancelled`
- agent：`agent.started`、`agent.message`、`agent.tool_called`、`agent.tool_result`、`agent.completed`
- worker：`worker.started`、`worker.completed`、`worker.error`、`worker.timeout`
- artifact/gate：`artifact.created`、`gate.started`、`gate.passed`、`gate.failed`

`RouterEvent` 里会带 `seq`、`type`、`title`、`message`、`source`、`severity`、`correlation`、`payload`、`created_at`。
