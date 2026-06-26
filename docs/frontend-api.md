# 前端 API 使用指南

本文档说明 Router 后端提供给前端调用的公开 API。内容按产品工作流组织，而不是按后端模块组织，方便前端完成以下流程：

- 创建任务
- 订阅执行进度
- 渲染时间线和 Agent 状态
- 读取 Artifact 元数据和内容
- 回复澄清问题
- 取消任务
- 查看最终报告
- 查看 trace / debug 信息

前端不应直接调用内部 worker、MCP Server、数据库、本地 Artifact 文件或 Main Agent function tools。

当前后端的主链路是 Main Agent 原生 tool loop：任务创建后，Runtime 在后台启动 Main Agent，Main Agent 通过 OpenAI 兼容 Chat Completions 的普通 tool calling 决定计划、调用 worker、运行 Quality Gate、写最终报告并收口任务状态。前端消费的是后端持久化后的公开事件流和 Artifact；不要依赖模型私有 chain-of-thought，也不要假设后端会暴露 OpenAI Responses API 的原始事件格式。

完整载荷类型请以 Router v1 契约文件为准：

- TypeScript 参考类型：[`schema/ts/router_contract.d.ts`](../schema/ts/router_contract.d.ts)
- JSON Schema 契约：
  - [`schema/task_state.schema.json`](../schema/task_state.schema.json)
  - [`schema/router_event.schema.json`](../schema/router_event.schema.json)
  - [`schema/artifact.schema.json`](../schema/artifact.schema.json)
  - [`schema/worker_input.schema.json`](../schema/worker_input.schema.json)
  - [`schema/worker_result.schema.json`](../schema/worker_result.schema.json)

不要在前端手写完整 schema 副本。优先从契约文件导入或生成类型；本文中的 JSON 仅作为调用示例。

## 本地基础地址

本地开发通常用以下命令启动 API：

```bash
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

本地前端开发建议使用：

```text
http://127.0.0.1:8000
```

完整的后端环境、数据库和 Artifact Store 准备步骤见 [`docs/local-dev.md`](local-dev.md)。

## 前端可调用接口

前端只应调用以下 Router 后端接口：

| Method | Path | 前端用途 |
| --- | --- | --- |
| `GET` | `/health` | 根路径健康检查 |
| `GET` | `/api/health` | API 健康检查 |
| `POST` | `/api/tasks` | 创建任务并获得任务句柄 |
| `GET` | `/api/tasks/{task_id}` | 读取当前 `TaskState` |
| `POST` | `/api/tasks/{task_id}/messages` | 追加用户后续消息 |
| `POST` | `/api/tasks/{task_id}/cancel` | 取消可取消的任务 |
| `GET` | `/api/tasks/{task_id}/events` | 通过 SSE 流式读取任务事件 |
| `GET` | `/api/tasks/{task_id}/artifacts` | 列出任务 Artifact 元数据 |
| `GET` | `/api/artifacts/{artifact_id}` | 读取 UTF-8 Artifact 内容 |
| `GET` | `/api/tasks/{task_id}/trace` | 读取紧凑时间线 / debug 投影 |

## 推荐前端工作流

```text
用户提交请求
  |
  v
POST /api/tasks
  |
  +-- response.task_id
  +-- response.events_url
  |
  v
用 SSE 打开 GET /api/tasks/{task_id}/events
  |
  +-- 根据 RouterEvent 渲染聊天区、时间线和 Agent Card
  +-- main_agent.message 是 Main Agent 可公开展示的发言
  +-- main_agent.tool_called / tool_result 是 Main Agent 决策步骤
  +-- 需要 status、gates、failures 或 artifact refs 时拉取 TaskState
  +-- 收到 artifact 事件或终态事件后刷新 Artifact 列表
  +-- 用户选中某个 Artifact 时按需读取内容
  |
  +-- 如果任务 waiting_user：POST /api/tasks/{task_id}/messages
  +-- 如果用户取消：POST /api/tasks/{task_id}/cancel
  |
  v
渲染 final_report 和交付 Artifact
```

推荐 UI 数据映射：

| 前端视图 | 主要数据源 |
| --- | --- |
| Chat Panel | 用户输入、`main_agent.message`、澄清事件、最终报告摘要 |
| Execution Timeline | SSE `RouterEvent` frame，可选叠加 `/trace` 投影 |
| Agent Cards | `main_agent.turn_started`、`main_agent.plan_updated`、`main_agent.tool_called`、`main_agent.tool_result`、`worker.*`、`gate.*` 和任务终态事件 |
| Artifact Panel | `GET /api/tasks/{task_id}/artifacts` 元数据和选中 Artifact 内容 |
| Final Report | `final_report` Artifact 内容 |
| Debug / Trace View | `GET /api/tasks/{task_id}/trace` |

## 健康检查

两个健康检查接口返回相同结构：

```http
GET /api/health
```

```json
{
  "status": "ok",
  "app": "router-backend",
  "env": "local"
}
```

前端 API 客户端优先使用 `/api/health`。`/health` 也可作为根路径健康检查。

## 创建任务

```http
POST /api/tasks
Content-Type: application/json
```

```json
{
  "message": "帮我实现一个带急停、故障锁存、自动/手动切换的输送线控制逻辑",
  "project_context": {
    "target_plc_language": "ST",
    "target_platform": "Codesys"
  }
}
```

`message` 必填，且不能为空白字符串。`project_context` 可选；如果不传，后端使用空项目上下文。

成功响应：

```http
201 Created
```

```json
{
  "task_id": "task_abc123",
  "status": "created",
  "events_url": "/api/tasks/task_abc123/events"
}
```

创建成功后应立即打开 `events_url`。任务执行会在后台调度，`POST /api/tasks` 的 HTTP 响应只是运行中任务的句柄，不是最终结果。

## 读取任务状态

```http
GET /api/tasks/{task_id}
```

返回当前 Router v1 `TaskState`。

前端常用字段：

| 字段 | 用途 |
| --- | --- |
| `status` | 任务顶层状态：`created`、`running`、`waiting_user`、`succeeded`、`partial_failed`、`failed`、`cancelled` |
| `phase` | 当前执行阶段，可用于时间线和加载状态文案 |
| `task_type` | Main Agent tool loop 或后端 domain tool 准备路径写入/规范化后的任务类型，可用于 badge 或路由显示 |
| `difficulty` | 当前难度、原因，以及是否要求 test / formal 等信号；它可能来自初始默认值、Main Agent 计划更新和后端策略提升 |
| `gates` | test / formal / regression 要求，以及通过或阻塞状态 |
| `current_artifacts` | 当前代码、最新报告、最终报告和所有 Artifact ID 的引用 |
| `unresolved_questions` | `waiting_user` 时需要展示给用户的问题 |
| `failures` | 开放或已解决的失败项，以及相关证据 Artifact |
| `trace` | 可用时包含 OpenAI trace ID 和 Main Agent run ID |
| `event_seq` | TaskState 已观测到的最新事件序号 |

大内容不会嵌入 `TaskState`。PLC 代码、测试报告、形式化报告、反例、patch、replay log 和最终报告正文都应通过 Artifact 引用读取。

示例片段：

```json
{
  "schema_version": "router.v1",
  "task_id": "task_abc123",
  "status": "running",
  "phase": "testing",
  "task_type": "new_plc_development",
  "current_artifacts": {
    "current_code": {
      "artifact_id": "artifact_code_v1",
      "type": "plc_code",
      "version": 1,
      "summary": "Structured Text motor control program."
    },
    "all_artifact_ids": ["artifact_raw_request", "artifact_code_v1"]
  },
  "event_seq": 7
}
```

## 追加用户消息

当任务等待用户澄清，或产品允许用户对非终态任务追加说明时，使用该接口。

```http
POST /api/tasks/{task_id}/messages
Content-Type: application/json
```

```json
{
  "message": "急停后需要人工复位才能重新启动。"
}
```

成功响应：

```json
{
  "task": {
    "schema_version": "router.v1",
    "task_id": "task_abc123",
    "status": "running",
    "phase": "planning"
  },
  "message_artifact_id": "artifact_user_message_001"
}
```

`task` 字段是完整 `TaskState`。用户消息也会持久化为 Artifact；需要做审计或 trace 时可使用 `message_artifact_id`。

## 取消任务

```http
POST /api/tasks/{task_id}/cancel
```

成功响应是更新后的 `TaskState`：

```json
{
  "schema_version": "router.v1",
  "task_id": "task_abc123",
  "status": "cancelled",
  "phase": "completed",
  "completed_at": "2026-06-18T10:00:00Z"
}
```

`created`、`running`、`waiting_user` 等可取消状态可以被取消。对已经 `cancelled` 的任务再次取消是幂等的，会返回当前取消状态。对已经 `succeeded`、`partial_failed` 或 `failed` 的任务取消会返回冲突。

## 订阅任务事件

```http
GET /api/tasks/{task_id}/events
Accept: text/event-stream
```

响应类型：

```text
text/event-stream
```

每个 SSE event frame 的结构：

```text
id: <event.seq>
event: <event.type>
data: <RouterEvent JSON>

```

示例：

```text
id: 3
event: worker.started
data: {"schema_version":"router.v1","event_id":"event_worker_started_001","task_id":"task_abc123","seq":3,"type":"worker.started","source":{"type":"worker","id":"worker_job_001","worker_type":"plc-dev"},"severity":"info","visibility":"user","title":"PLC development started","message":null,"correlation":{"worker_job_id":"worker_job_001"},"payload":{"worker_type":"plc-dev","worker_job_id":"worker_job_001","summary":"Generating Structured Text code."},"created_at":"2026-06-18T10:00:00Z"}

```

`data` 是 Router v1 `RouterEvent`。完整类型见 [`RouterEvent`](../schema/ts/router_contract.d.ts)。

重要事件分组：

| 事件分组 | 示例 | UI 用途 |
| --- | --- | --- |
| 任务生命周期 | `task.created`、`task.waiting_user`、`task.succeeded`、`task.partial_failed`、`task.failed`、`task.cancelled` | 顶层状态和终态展示 |
| Main Agent 生命周期 | `agent.started`、`agent.turn_started`、`agent.completed` | 展示主 Agent 已启动、正在进行第几轮、已完成 |
| Main Agent 公开发言 | `agent.message` | 渲染到聊天区或步骤说明区；这是公开摘要，不是隐藏 chain-of-thought |
| Main Agent 计划和决策 | `agent.plan_updated`、`agent.clarification_requested`、`agent.tool_called`、`agent.tool_result` | 展示计划、澄清请求、工具调用意图和工具结果 |
| Worker 生命周期 | `worker.started`、`worker.completed`、`worker.error`、`worker.timeout` | Agent Card 和 worker 时间线 |
| Artifact | `artifact.created`、`artifact.available`、`artifact.failed` | 刷新 Artifact Panel |
| Quality Gate | `gate.started`、`gate.passed`、`gate.failed` | 验证状态 |
| 修复轮次 | `repair.round_started`、`repair.round_completed`、`repair.round_failed` | 修复循环可视化 |

### Main Agent 步骤流

这次重构后，前端不需要等待黑盒式最终状态。Main Agent 的公开执行过程会作为普通 Router 事件持久化并通过 SSE 推送：

```text
task.created
agent.started
agent.turn_started
agent.message
agent.tool_called        list_files
agent.tool_result        list_files
agent.turn_started
agent.message
agent.tool_called        plc_dev
worker.started
artifact.created
worker.completed
agent.tool_result        plc_dev
...
gate.started
gate.passed
agent.final_response
agent.completed          final_report_artifact_id, main_agent_log_artifact_id
task.succeeded
```

常用 Main Agent event payload：

| Event type | 关键 payload 字段 | 前端建议 |
| --- | --- | --- |
| `agent.started` | `task_id`、`main_agent_run_id`、`phase`、`status`；trace 字段在事件关联信息和 `TaskState.trace` 中 | 初始化 Agent Card 和 trace 关联 |
| `agent.turn_started` | `task_id`、`turn_index`、`phase` | 可作为时间线分隔点；通常不需要在聊天区单独展示 |
| `agent.message` | `task_id`、`turn_index`、`phase`、`content` | 渲染为 Main Agent 的公开消息 |
| `agent.plan_updated` | `task_id`、`plan` | 更新计划面板；也可作为时间线事件 |
| `agent.tool_called` | `task_id`、`turn_index`、`tool_name`、`rationale_summary`、`arguments`、`input_artifact_ids` | 展示“正在调用某工具/worker”；参数已清洗但仍建议做折叠展示 |
| `agent.tool_result` | `task_id`、`turn_index`、`tool_name`、`status`、`summary`、`artifact_ids`、`failure_ids`、`worker_job_id`、`worker_type`、`next_recommended_action` | 更新步骤状态；`status=failed` 或 `rejected` 不一定是任务失败，Main Agent 可能会在下一轮修正 |
| `agent.completed` | `task_id`、`main_agent_run_id`、`final_task_status`、`summary`、`final_report_artifact_id`、`main_agent_log_artifact_id`、`token_usage`、`token_usage_scope` | 读取最终报告；`token_usage` 仅统计本次 Main Agent provider 调用，不包含 worker/MCP token；注意终态任务事件可能紧随其后才到达 |

Runtime finalization 写入的 `final_report` 和 `main_agent_log` 不要求额外产生 `artifact.created` 事件。前端应优先从 `agent.completed.payload.final_report_artifact_id` 或刷新后的 Artifact 列表中发现最终报告。

`agent.completed.payload.token_usage` 为可选字段，provider 未返回 usage 时不会出现。存在时形如：

```json
{
  "token_usage": {
    "input_tokens": 123,
    "output_tokens": 45,
    "total_tokens": 168
  },
  "token_usage_scope": "main_agent"
}
```

Main Agent 工具名是后端实现细节，但当前前端可用于展示的常见值包括：

```text
update_plan
request_clarification
list_files
read_file
write_file
apply_patch
exec_command
git_status
read_artifact
write_artifact
plc_dev
plc_test
plc_formal
plc_repair
run_quality_gate
write_final_report
```

前端应把工具结果当作“步骤状态”，不要把 `agent.tool_result.payload.details` 当作稳定 UI 契约。稳定字段优先使用上表列出的 `summary`、`artifact_ids`、`failure_ids`、`worker_job_id`、`worker_type` 和 `status`。

### 浏览器 EventSource

```ts
const source = new EventSource(`${baseUrl}${eventsUrl}`);
let lastSeq = 0;

source.addEventListener("agent.message", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  renderAgentMessage(event.payload.content);
});

source.addEventListener("agent.tool_called", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  markStepRunning({
    toolName: event.payload.tool_name,
    summary: event.payload.rationale_summary,
    artifactIds: event.payload.input_artifact_ids,
  });
});

source.addEventListener("agent.tool_result", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  markStepFinished({
    toolName: event.payload.tool_name,
    status: event.payload.status,
    summary: event.payload.summary,
    artifactIds: event.payload.artifact_ids,
  });
});

source.addEventListener("agent.completed", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  queueFinalReportFetch(event.payload.final_report_artifact_id);
  renderTokenUsage(event.payload.token_usage);
});

source.addEventListener("worker.started", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  // 渲染时间线行或更新 Agent Card。
});

source.addEventListener("task.succeeded", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  source.close();
  // 拉取 Artifacts 并渲染最终报告。
});
```

`EventSource` 会自动使用 event `id` 进行浏览器托管的断线重连。如果前端自己保存 cursor，并希望显式 replay，可用 `after_seq` 重新打开：

```ts
const source = new EventSource(
  `${baseUrl}/api/tasks/${taskId}/events?after_seq=${lastSeq}`,
);
```

对于可以设置 header 的自定义 SSE 客户端，后端也接受 `Last-Event-ID: <seq>`。如果同时提供 `after_seq` 和 `Last-Event-ID`，以 `after_seq` 为准。

心跳 frame 是注释形式：

```text
: keepalive

```

前端应忽略心跳 frame，不要用它更新 UI 状态。默认面向前端的事件流会过滤 `visibility=internal` 的事件。

## 列出任务 Artifacts

```http
GET /api/tasks/{task_id}/artifacts
```

返回该任务的 Artifact 元数据：

```json
{
  "task_id": "task_abc123",
  "artifacts": [
    {
      "schema_version": "router.v1",
      "artifact_id": "artifact_code_v1",
      "task_id": "task_abc123",
      "type": "plc_code",
      "version": 1,
      "name": "motor_control_v1.st",
      "status": "available",
      "visibility": "user",
      "storage": {
        "provider": "local",
        "uri": "local://artifacts/task_abc123/plc_code_v1.st",
        "mime_type": "text/plain",
        "size_bytes": 2048,
        "content_hash": "sha256:..."
      },
      "summary": "Structured Text implementation for motor start/stop.",
      "parent_artifact_ids": [],
      "created_by": {
        "type": "worker",
        "worker_type": "plc-dev",
        "worker_job_id": "worker_job_001"
      },
      "metadata": {}
    }
  ]
}
```

列表响应有意不包含 Artifact 内容。前端应使用它填充 tab、卡片、badge、版本、摘要和元数据。只有当用户选中某个 Artifact，或最终报告需要渲染时，再按需读取内容。

响应中可能包含 `visibility=internal` 的 Artifact，例如 `main_agent_log`。前端 UI 应根据 `visibility` 决定是否默认展示。

`main_agent_log` 是 Main Agent replay/debug Artifact，通常为内部可见。它记录公开消息、工具调用、工具结果和最终输出摘要，用于 debug 或审计，不适合作为普通用户交付页面的主内容。

## 读取 Artifact 内容

```http
GET /api/artifacts/{artifact_id}
```

成功的 UTF-8 文本响应：

```json
{
  "artifact": {
    "artifact_id": "artifact_code_v1",
    "task_id": "task_abc123",
    "type": "plc_code",
    "version": 1,
    "visibility": "user",
    "summary": "Structured Text implementation for motor start/stop."
  },
  "content": "PROGRAM MotorControl\n  ...\nEND_PROGRAM\n",
  "content_encoding": "utf-8",
  "mime_type": "text/plain",
  "size_bytes": 2048,
  "content_hash": "sha256:..."
}
```

JSON Artifact 的 `content` 仍以 UTF-8 字符串返回。需要时由前端解析：

```ts
const artifactResponse = await fetch(`${baseUrl}/api/artifacts/${artifactId}`);
const payload = await artifactResponse.json();
const content =
  payload.mime_type === "application/json"
    ? JSON.parse(payload.content)
    : payload.content;
```

Artifact Panel 渲染建议：

| Artifact type | 常见渲染方式 |
| --- | --- |
| `plc_code` | Monaco / editor 视图、语法高亮、复制或下载操作 |
| `io_contract` | 表格或结构化 JSON viewer |
| `test_cases` | 测试用例列表或 JSON viewer |
| `test_report` | Markdown / 报告 viewer 和 pass/fail 摘要 |
| `failing_trace` | 结构化 trace viewer |
| `formal_report` | Markdown / 报告 viewer 和性质状态摘要 |
| `counterexample` | JSON trace / 表格 viewer |
| `patch` | Diff viewer |
| `repair_summary` | Markdown / 报告摘要 |
| `gate_report` | 验证检查清单 |
| `final_report` | 最终交付页面 |
| `main_agent_log` | 仅用于内部 debug viewer |

## 最终报告

任务进入终态前后，如果最终报告生成完成，应能看到一个 `final_report` Artifact。可以从以下位置发现它：

- `TaskState.current_artifacts.final_report`
- Artifact 列表中 `type` 为 `final_report` 的条目
- `agent.completed` 事件 payload 中的 `final_report_artifact_id`

然后读取内容：

```http
GET /api/artifacts/{final_report_artifact_id}
```

最终报告由 runtime finalization 写入 Artifact，并在同一收口路径中产生 `agent.completed`。随后通常会看到 `task.succeeded`、`task.partial_failed` 或 `task.failed` 等终态事件。

最终报告内容是已完成任务页面的推荐主数据源。当前内容是 JSON payload，顶层字段包括：

- `kind`
- `schema_version`
- `report_version`
- `task_id`
- `main_agent_run_id`
- `final_task_status`
- `user_goal`
- `classification`
- `summary`
- `plan`
- `decisions`
- `delivery_artifacts`
- `validation_summary`
- `repair_summary`
- `assumptions`
- `unresolved_items`
- `gate_summary`
- `trace_refs`
- `main_agent_output_summary`

最终报告应通过 Artifact ID 引用大内容，而不是嵌入完整 PLC 代码、长报告、patch、worker log 或 replay log。

## Trace Summary

```http
GET /api/tasks/{task_id}/trace
```

该接口用于重建时间线、调试和查看关联关系。它返回紧凑投影，而不是完整 Artifact 内容：

```json
{
  "task_id": "task_abc123",
  "openai_trace_id": "trace_001",
  "main_agent_run_ids": ["main_agent_run_001"],
  "latest_main_agent_run_id": "main_agent_run_001",
  "terminal_event_id": "event_task_succeeded",
  "terminal_event_type": "task.succeeded",
  "main_agent_runs": [],
  "worker_jobs": [],
  "artifacts": [],
  "gate_results": [],
  "events": []
}
```

典型前端用途：

- 页面刷新或断线重连后重建确定性的执行时间线
- 展示哪个 worker job 产出了哪些 Artifacts
- 将 gate failure 与证据 Artifact ID 关联起来
- 不加载大内容也能调试任务执行过程

Trace summary 有意只包含 Artifact 元数据和 event payload keys，不包含完整 PLC 代码、测试报告、形式化报告、patch 或 replay log 内容。

## 错误处理

| 接口分组 | 状态码 | 含义 |
| --- | --- | --- |
| 请求校验 | `422` | 空白消息、非法 body、非法 path/query 类型或非法 `after_seq` |
| SSE cursor 校验 | `400` | `Last-Event-ID` 不是合法序号 |
| 任务读取 / 变更 | `404` | 任务 ID 不存在 |
| Artifact 读取 | `404` | Artifact ID 或 task ID 不存在 |
| 任务变更 | `409` | 变更冲突，例如向终态任务追加消息，或取消已完成任务 |
| Artifact 读取 | `409` | Artifact 元数据指向非法或不支持的存储 |
| Artifact 读取 | `415` | Artifact 内容不是 UTF-8 文本 |
| Artifact 读取 | `500` | Artifact 内容读取失败 |

FastAPI 校验错误使用标准的 `detail` JSON 结构。应用错误通常返回：

```json
{
  "detail": "human-readable error message"
}
```

前端处理建议：

- `422`：停留在表单页并展示校验反馈。
- `404`：将任务或 Artifact 视为不可用，并引导用户回到任务列表或输入页。
- `409`：先刷新 TaskState，再判断是否允许重试。
- `415`：展示元数据，并提示当前内容无法内联预览。
- SSE 断线：使用最后观测到的 `seq` 通过 `after_seq` 重连。

## 类型引用表

使用 [`schema/ts/router_contract.d.ts`](../schema/ts/router_contract.d.ts) 中的这些 TypeScript 接口和类型：

| 前端关注点 | TypeScript 引用 |
| --- | --- |
| 任务状态页 | `TaskState`、`TaskStatus`、`TaskPhase`、`ProjectContext` |
| 时间线事件 | `RouterEvent`、`EventType`、`EventCorrelation` |
| Artifact Panel | `Artifact`、`ArtifactRef`、`ArtifactType`、`ArtifactVisibility` |
| 失败和问题 | `Failure`、`ClarificationQuestion`、`Assumption` |
| state 中的 worker / gate 摘要 | `WorkerJobRef`、`GateState`、`CurrentArtifacts` |

后端校验事实来源是 [`backend/app/models/router_schema.py`](../backend/app/models/router_schema.py)。[`schema/`](../schema/) 下的 JSON Schema 文件是面向非 TypeScript 工具或客户端的语言无关契约。

## 最小 Fetch Helpers

```ts
const baseUrl = "http://127.0.0.1:8000";

export async function createTask(message: string) {
  const response = await fetch(`${baseUrl}/api/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      project_context: {
        target_plc_language: "ST",
        target_platform: "Codesys",
      },
    }),
  });

  if (!response.ok) {
    throw new Error(`create task failed: ${response.status}`);
  }
  return response.json();
}

export async function getTask(taskId: string) {
  const response = await fetch(`${baseUrl}/api/tasks/${taskId}`);
  if (!response.ok) {
    throw new Error(`get task failed: ${response.status}`);
  }
  return response.json();
}

export async function listArtifacts(taskId: string) {
  const response = await fetch(`${baseUrl}/api/tasks/${taskId}/artifacts`);
  if (!response.ok) {
    throw new Error(`list artifacts failed: ${response.status}`);
  }
  return response.json();
}

export async function readArtifact(artifactId: string) {
  const response = await fetch(`${baseUrl}/api/artifacts/${artifactId}`);
  if (!response.ok) {
    throw new Error(`read artifact failed: ${response.status}`);
  }
  return response.json();
}
```
