# 前端 API 使用指南

本文档记录当前 Router 后端实际挂载给前端使用的 HTTP/SSE API，以及前端代码当前如何消费这些接口。内容以源码为准：

- 后端入口：[`backend/app/main.py`](../backend/app/main.py)
- 后端路由：[`backend/app/api/`](../backend/app/api/)
- 前端 API helper：[`frontend/src/api/router/`](../frontend/src/api/router/)
- 前端工作区：[`frontend/src/features/task-workspace/`](../frontend/src/features/task-workspace/)
- TypeScript 契约参考：[`schema/ts/router_contract.d.ts`](../schema/ts/router_contract.d.ts)

当前默认契约版本是 `router.v2`。前端不要手写完整 schema 副本，应从 `schema/ts/router_contract.d.ts` 导入或生成类型。

前端不应直接调用 worker、MCP Server、数据库、runtime service、repository、Main Agent function tools 或本地 workspace 文件路径。

## 本地基础地址

后端本地启动：

```bash
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

浏览器前端的 API 基础地址来自 `VITE_ROUTER_API_BASE_URL`。未配置时为空字符串，走同源 `/api`；Vite 开发服务器会把 `/api` 代理到 `http://127.0.0.1:8000`。

```text
VITE_ROUTER_API_BASE_URL=http://127.0.0.1:8000
```

## 当前挂载的前端接口

下面只列当前新前端工作流会使用的接口。未列出的后端路由不作为新前端集成依据，避免被误用。

| Method | Path | 当前前端用途 |
| --- | --- | --- |
| `GET` | `/health` | 根路径健康检查 |
| `GET` | `/api/health` | 前端健康检查主入口 |
| `GET` | `/api/subagents/status` | Worker 路由和远端 subagent 可达性 |
| `POST` | `/api/sessions` | 当前 UI 的主创建入口 |
| `GET` | `/api/sessions` | 当前 UI 的最近会话列表 |
| `GET` | `/api/sessions/{session_id}` | 恢复会话和最新任务 |
| `DELETE` | `/api/sessions/{session_id}` | 删除会话及其关联 run/task |
| `POST` | `/api/sessions/{session_id}/messages` | 当前 UI 的主续聊入口，新建一个 run/task |
| `GET` | `/api/sessions/{session_id}/events` | 当前 UI 的主 SSE 流 |
| `GET` | `/api/tasks/{task_id}` | 读取当前 `TaskState` |
| `POST` | `/api/tasks/{task_id}/cancel` | 取消当前 task |
| `GET` | `/api/tasks/{task_id}/trace` | trace/workspace/worker card 调试投影 |

## 当前前端调用方式

当前 React 工作区是 session-first：

1. 页面启动先 `GET /api/health`。
2. 尝试从 `localStorage` 恢复 `router-agent.active-session-id`，调用 `GET /api/sessions/{session_id}`。
3. 没有本地会话时调用 `GET /api/sessions?limit=50`，选择最近会话。
4. 用户提交新请求时调用 `POST /api/sessions`。
5. 前端保存 `session_id` 和 `task_id`，打开 `GET /api/sessions/{session_id}/events?after_seq=0`。
6. 收到 task/agent/worker/gate 事件后，按需刷新 `GET /api/tasks/{task_id}` 和 `GET /api/tasks/{task_id}/trace`。
7. 用户续聊时调用 `POST /api/sessions/{session_id}/messages`，后端在同一 session 下创建新的 task/run。
8. 取消只作用于当前 task：`POST /api/tasks/{task_id}/cancel`。
9. `/api/subagents/status` 每 15 秒轮询一次，用于 Subagents 面板。

## 推荐产品工作流

```text
用户提交第一条消息
  |
  v
POST /api/sessions
  |
  +-- response.session.session_id
  +-- response.task_id / response.run_id
  +-- response.events_url = /api/sessions/{session_id}/events
  |
  v
打开 session SSE
GET /api/sessions/{session_id}/events?after_seq=0
  |
  +-- 按 RouterEvent 渲染对话、时间线、worker cards
  +-- 收到关键事件后刷新 TaskState / TraceSummary
  +-- session stream 在单次 run 终态后继续保持，等待下一轮消息
  |
  +-- 用户续聊：POST /api/sessions/{session_id}/messages
  +-- 用户取消当前 run：POST /api/tasks/{task_id}/cancel
  |
  v
读取最新 task state 和 trace，渲染最终响应、workspace path、gate 和 worker 结果
```

推荐 UI 数据映射：

| 前端视图 | 主要数据源 |
| --- | --- |
| Chat / transcript | session SSE 中的 `task.created`、`agent.message`、`agent.final_response`、`agent.completed` |
| 最近任务列表 | `GET /api/sessions` |
| Execution Timeline | session SSE `RouterEvent` |
| Worker Cards | `TaskState.active_worker_jobs`、SSE worker 事件、`TraceSummary.worker_jobs`、`/api/subagents/status` |
| Workspace | `TaskState.current_files`、`TraceSummary.files` |
| Trace / Debug | `GET /api/tasks/{task_id}/trace` |
| Final Report | 当前是 workspace path：`current_files.final_report` 或 `agent.completed.payload.final_report_path` |

## 健康检查

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

`/health` 和 `/api/health` 返回相同结构。它们是进程级健康检查，不表示数据库、worker 或远端 subagent 一定可用。

## Session API

### 创建会话

```http
POST /api/sessions
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

`message` 必填且不能为空白字符串。`project_context` 可选；前端通常传入 PLC 语言和平台。

成功响应：

```http
201 Created
```

```json
{
  "session": {
    "schema_version": "router.v2",
    "session_id": "session_abc123",
    "title": "帮我实现一个带急停、故障锁存、自动/手动切换的输送线控制逻辑",
    "status": "active",
    "latest_task_id": "task_001",
    "latest_run_id": "task_001",
    "event_seq": 1,
    "runs": []
  },
  "task": {
    "schema_version": "router.v2",
    "task_id": "task_001",
    "session_id": "session_abc123",
    "status": "created",
    "phase": "intake"
  },
  "task_id": "task_001",
  "run_id": "task_001",
  "events_url": "/api/sessions/session_abc123/events"
}
```

`run_id` 当前等于 `task_id`。创建成功后应打开 `events_url`；HTTP 响应只是运行句柄，不是最终结果。

### 读取会话列表

```http
GET /api/sessions?limit=50
```

`limit` 范围是 1 到 100。返回按 `updated_at` 倒序排列的会话：

```json
{
  "sessions": [
    {
      "schema_version": "router.v2",
      "session_id": "session_abc123",
      "title": "输送线控制逻辑",
      "status": "active",
      "latest_task_id": "task_002",
      "latest_run_id": "task_002",
      "event_seq": 23,
      "runs": [
        {
          "run_id": "task_001",
          "task_id": "task_001",
          "status": "succeeded",
          "user_message": "初始需求",
          "final_response": "已完成。",
          "created_at": "2026-07-01T02:00:00Z",
          "updated_at": "2026-07-01T02:01:00Z",
          "completed_at": "2026-07-01T02:01:00Z"
        }
      ]
    }
  ]
}
```

前端当前用 `AgentSession.title`、`status`、`latest_task_id`、`runs[].user_message` 和 `updated_at` 构建左侧列表。

### 读取单个会话

```http
GET /api/sessions/{session_id}
```

成功响应：

```json
{
  "session": {
    "session_id": "session_abc123",
    "status": "active",
    "latest_task_id": "task_002"
  },
  "latest_task": {
    "task_id": "task_002",
    "session_id": "session_abc123",
    "status": "running",
    "phase": "developing"
  }
}
```

会话不存在返回 `404`。

### 追加会话消息

```http
POST /api/sessions/{session_id}/messages
Content-Type: application/json
```

```json
{
  "message": "继续补充：急停解除后必须人工复位才能重新启动。"
}
```

成功响应：

```json
{
  "session": {
    "session_id": "session_abc123",
    "latest_task_id": "task_003",
    "latest_run_id": "task_003",
    "status": "active"
  },
  "task": {
    "task_id": "task_003",
    "session_id": "session_abc123",
    "status": "created",
    "phase": "intake"
  },
  "task_id": "task_003",
  "run_id": "task_003"
}
```

这个接口会在同一 session 下创建新的 task/run，并启动 runtime。不要把 session 级续聊理解成修改已终态的旧 task。

如果 session 不存在返回 `404`；如果 session 不是 `active` 返回 `409`；空白消息返回 `422`。

### 删除会话

```http
DELETE /api/sessions/{session_id}
```

成功返回 `204 No Content`。后端会删除会话、runs 以及关联 tasks/workspace。当前前端删除左侧会话时调用该接口。

## Task State / Cancel API

### 读取 task state

```http
GET /api/tasks/{task_id}
```

返回当前 `TaskState`。前端常用字段：

| 字段 | 用途 |
| --- | --- |
| `task_id` / `session_id` | 当前 run 和会话关联 |
| `status` | `created`、`running`、`waiting_user`、`succeeded`、`partial_failed`、`failed`、`cancelled` |
| `phase` | `intake`、`planning`、`developing`、`testing`、`formal_verifying`、`repairing`、`quality_gate`、`synthesizing`、`completed` 等 |
| `title` / `raw_user_request` / `normalized_goal` | 列表标题、当前任务摘要 |
| `task_type` / `difficulty` | badge、调度状态、测试/形式化需求展示 |
| `project_context` | PLC 语言、平台、workspace 设置 |
| `workspace` / `execution_policy` | workspace 根路径和执行权限提示 |
| `current_files` | 当前代码、报告、最终报告等 workspace-relative path |
| `runtime_limits` | repair round、worker call、并发预算 |
| `gates` | test/formal/regression/final gate 状态 |
| `active_worker_jobs` / `completed_worker_job_ids` | worker card 状态 |
| `unresolved_questions` | `waiting_user` 时展示澄清问题 |
| `failures` | 失败、证据 path 和修复状态 |
| `trace` | `openai_trace_id`、`main_agent_run_ids` |
| `event_seq` | task 事件最新序号 |

`current_files` 是当前契约的重点，字段值是 workspace 相对路径，不是 artifact ID：

```json
{
  "current_files": {
    "raw_user_request": ".router/requests/task_001_raw_user_request.json",
    "current_code": "src/plc_code.st",
    "latest_test_report": ".router/reports/plc_test_report.json",
    "latest_gate_report": ".router/reports/quality_gate.json",
    "final_report": ".router/runs/task_001/final_report.json",
    "main_agent_log": ".router/runs/task_001/main_agent_replay_log.json",
    "all_paths": [
      ".router/requests/task_001_raw_user_request.json",
      "src/plc_code.st",
      ".router/runs/task_001/final_report.json"
    ]
  }
}
```

当前公开 HTTP API 没有挂载通用 workspace file content 读取接口；前端应把这些 path 作为 workspace/trace 引用来展示。

### 取消 task

```http
POST /api/tasks/{task_id}/cancel
```

成功响应是更新后的 `TaskState`：

```json
{
  "task_id": "task_abc123",
  "status": "cancelled",
  "phase": "completed",
  "completed_at": "2026-07-01T02:00:00Z"
}
```

`created`、`running`、`waiting_user` 可取消。已经 `cancelled` 的 task 再次取消是幂等的。已经 `succeeded`、`partial_failed` 或 `failed` 的 task 取消会返回 `409`。

## SSE 事件流

当前新前端只使用 session 级 SSE：

| Endpoint | 序号语义 | 当前用途 |
| --- | --- | --- |
| `GET /api/sessions/{session_id}/events` | session 内递增序号 | 当前 UI 主事件流，可跨多个 run |

请求：

```http
GET /api/sessions/{session_id}/events?after_seq=0
Accept: text/event-stream
```

响应类型：

```text
text/event-stream
```

每个事件 frame：

```text
id: <event.seq>
event: <event.type>
data: <RouterEvent JSON>

```

示例：

```text
id: 3
event: agent.tool_result
data: {"schema_version":"router.v2","event_id":"event_001","task_id":"task_001","seq":3,"type":"agent.tool_result","source":{"type":"main_agent","id":"main_agent_run_001"},"severity":"info","visibility":"user","title":"Tool result","message":"PLC development completed.","correlation":{"session_id":"session_001","run_id":"task_001","main_agent_run_id":"main_agent_run_001"},"payload":{"task_id":"task_001","run_id":"task_001","tool_name":"plc_dev","status":"applied","summary":"PLC code written.","written_paths":["src/plc_code.st"],"report_paths":[]},"created_at":"2026-07-01T02:00:00Z"}

```

断线恢复：

- `after_seq=N` 表示只推送 `seq > N` 的可见事件。
- 浏览器 `EventSource` 会自动带 `Last-Event-ID`；自定义客户端也可显式传 header。
- 同时提供 `after_seq` 和 `Last-Event-ID` 时，以 `after_seq` 为准。
- 非法 `Last-Event-ID` 返回 `400`；非法 `after_seq` 返回 FastAPI 校验错误。

心跳 frame：

```text
: keepalive

```

心跳只表示连接还活着，前端应忽略它，不要更新 UI 状态。

默认 SSE 只推送 `visibility=user` 事件，过滤 `visibility=internal`。session stream 会在单次 run 终态后继续保持连接，以等待下一轮消息。

### session event sequence

session stream 会把关联 task 的事件复制到 session 事件日志，并重新分配 session 级 `seq`。前端应以 session stream 的 `seq` 作为重连 cursor。

session 事件会补充：

- `payload.session_id`
- `payload.run_id`
- `correlation.session_id`
- `correlation.run_id`

### 常用事件类型

当前新链路使用 `agent.*` 事件；新前端只需要监听下表这些事件分组。

| 分组 | 示例 | UI 用途 |
| --- | --- | --- |
| task 生命周期 | `task.created`、`task.updated`、`task.waiting_user`、`task.succeeded`、`task.partial_failed`、`task.failed`、`task.cancelled` | 顶层状态、用户消息、终态 |
| agent 生命周期 | `agent.started`、`agent.turn_started`、`agent.completed` | 主 Agent 运行状态 |
| agent 公开发言 | `agent.message`、`agent.final_response` | 聊天区展示；不是隐藏 chain-of-thought |
| agent 计划和工具 | `agent.plan_updated`、`agent.tool_called`、`agent.tool_result`、`agent.stop_blocked` | 过程面板、工具步骤、阻塞原因 |
| worker | `worker.job_created`、`worker.started`、`worker.progress`、`worker.completed`、`worker.error`、`worker.timeout`、`worker.cancelled` | Worker Cards 和时间线 |
| gate | `gate.started`、`gate.passed`、`gate.failed` | 验证状态 |
| repair | `repair.round_started`、`repair.round_completed`、`repair.round_failed` | 修复轮次 |

### 关键 payload

| Event type | 当前前端依赖字段 |
| --- | --- |
| `task.created` | `message`、`task_id`、`session_id`、`run_id`、`status`、`raw_user_request_path` |
| `agent.message` | `content`、`turn_index`、`phase` |
| `agent.final_response` | `content` |
| `agent.plan_updated` | `plan`、`summary` |
| `agent.tool_called` | `tool_name`、`turn_index`、`rationale_summary`、`arguments`、`input_paths` |
| `agent.tool_result` | `tool_name`、`status`、`summary`、`read_paths`、`written_paths`、`report_paths`、`failure_ids`、`worker_job_id`、`worker_type`、`details` |
| `agent.completed` | `final_task_status`、`summary`、`final_report_path`、`main_agent_log_path`、`token_usage`、`token_usage_scope` |
| worker events | `worker_type`、`worker_job_id`、path arrays、summary/status |
| gate events | `gate_type`、`status`、`blocking`、`evidence_paths` |

`agent.tool_result.payload.details` 是调试辅助，不应作为稳定 UI 契约。优先使用 `summary`、path arrays、`failure_ids`、`worker_job_id`、`worker_type` 和 `status`。

浏览器连接示例：

```ts
const source = new EventSource(
  `${baseUrl}/api/sessions/${sessionId}/events?after_seq=${lastSeq}`,
);

source.addEventListener("agent.message", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  renderAgentMessage(event.payload.content);
});

source.addEventListener("agent.tool_result", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  updateToolStep({
    toolName: event.payload.tool_name,
    status: event.payload.status,
    summary: event.payload.summary,
    writtenPaths: event.payload.written_paths ?? [],
  });
});

source.addEventListener("task.succeeded", (message) => {
  const event = JSON.parse(message.data);
  lastSeq = Number(message.lastEventId || event.seq);
  refreshTaskAndTrace(event.task_id);
});
```

## Trace Summary

```http
GET /api/tasks/{task_id}/trace
```

该接口返回紧凑投影，不嵌入代码、报告、patch 或 replay log 正文：

```json
{
  "task_id": "task_abc123",
  "openai_trace_id": "trace_001",
  "main_agent_run_ids": ["main_agent_run_001"],
  "latest_main_agent_run_id": "main_agent_run_001",
  "terminal_event_id": "event_task_succeeded",
  "terminal_event_type": "task.succeeded",
  "main_agent_runs": [
    {
      "main_agent_run_id": "main_agent_run_001",
      "openai_trace_id": "trace_001",
      "final_report_path": ".router/runs/task_abc123/final_report.json",
      "replay_log_path": ".router/runs/task_abc123/main_agent_replay_log.json",
      "error_event_ids": []
    }
  ],
  "worker_jobs": [],
  "files": [
    {
      "path": "src/plc_code.st",
      "exists": true,
      "size_bytes": 2048,
      "mime_type": "text/plain"
    }
  ],
  "gate_results": [],
  "events": [
    {
      "event_id": "event_001",
      "seq": 1,
      "type": "task.created",
      "payload_keys": ["message", "raw_user_request_path", "run_id", "session_id", "status", "task_id"]
    }
  ]
}
```

注意字段名是 `files`，不是 `artifacts`。前端当前用它来渲染 workspace path、worker job、gate result 和事件摘要。

## Subagent Status

```http
GET /api/subagents/status
```

响应示例：

```json
{
  "mode": "subagent",
  "base_url": "http://127.0.0.1:8080",
  "probe": {
    "method": "OPTIONS",
    "path": "/api/chat/stream",
    "scope": "transport_reachability",
    "status": "online",
    "online": true,
    "latency_ms": 12,
    "status_code": 405,
    "error": null,
    "checked_at": "2026-07-01T02:00:00+00:00"
  },
  "workers": [
    {
      "worker_type": "plc-dev",
      "agent_id": "智能开发智能体",
      "route": "subagent",
      "status": "online",
      "online": true,
      "latency_ms": 12,
      "status_code": 405,
      "error": null,
      "probe_scope": "transport_reachability"
    }
  ]
}
```

如果 worker 走 `mock` 或 `real`，`online` 通常为 `null`，`status` 直接显示路由值。探测失败不会让接口返回非 2xx，而是写入 `probe.status`、`online=false` 和 `error`。

## Final Report

当前最终报告首先是 workspace file，不是 artifact ID。可从以下位置发现：

- `TaskState.current_files.final_report`
- `agent.completed.payload.final_report_path`
- `TraceSummary.main_agent_runs[].final_report_path`

最终报告 JSON 当前核心字段：

- `kind`
- `schema_version`
- `report_version`
- `created_at`
- `task_id`
- `main_agent_run_id`
- `final_task_status`
- `user_goal`
- `classification`
- `summary`
- `plan`
- `decisions`
- `delivery_files`
- `validation_summary`
- `repair_summary`
- `assumptions`
- `unresolved_items`
- `gate_summary`
- `trace_refs`
- `main_agent_output_summary`

`delivery_files` 中的条目也是 workspace path 摘要。当前未挂载 workspace file content API，因此前端能展示 path、文件存在性和摘要投影；若需要在浏览器读取最终报告正文，需要后端补充或挂载相应内容读取接口。

## 错误处理

| 接口分组 | 状态码 | 含义 |
| --- | --- | --- |
| 请求校验 | `422` | 空白 message、非法 body、非法 path/query 类型、`limit` 越界、`after_seq` 小于 0 |
| SSE cursor | `400` | `Last-Event-ID` 不是合法非负整数 |
| 会话/task/trace 读取 | `404` | session 或 task 不存在 |
| task / session mutation | `409` | 写入冲突、非 active session、取消不可取消的 task |
| trace 投影 | `500` | 未捕获投影错误 |

FastAPI 校验错误使用标准 `detail` 结构。应用错误通常是：

```json
{
  "detail": "human-readable error message"
}
```

当前前端 `requestJson` 会：

- 自动为有 body 的请求补 `Content-Type: application/json`。
- 非 2xx 时解析 `detail` 并抛 `RouterApiError`。
- `204` 返回 `undefined`。

## 类型引用

前端类型参考：

| 前端关注点 | TypeScript 类型 |
| --- | --- |
| 会话 | `AgentSession`、`AgentSessionRunRef`、`AgentSessionStatus` |
| task 状态 | `TaskState`、`TaskStatus`、`TaskPhase`、`ProjectContext`、`CurrentFiles` |
| 事件流 | `RouterEvent`、`EventType`、`EventCorrelation` |
| worker / gate | `WorkerType`、`WorkerJobRef`、`GateState` |
| trace helper 类型 | `TaskTraceSummary` 等在 `frontend/src/api/router/types.ts` 中维护 |

后端 Pydantic 源码是运行时校验事实来源：[`backend/app/models/router_schema.py`](../backend/app/models/router_schema.py)。JSON Schema 文件仍可作为语言无关契约参考，但新前端集成应以本文列出的接口和 `router.v2` 类型为准。

## 最小 Fetch Helpers

```ts
const baseUrl = import.meta.env.VITE_ROUTER_API_BASE_URL?.replace(/\/$/, "") ?? "";

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${baseUrl}${path}`, { ...init, headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => undefined);
    throw new Error(payload?.detail ?? `Router API failed: ${response.status}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function createSession(message: string, projectContext = {}) {
  return requestJson("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ message, project_context: projectContext }),
  });
}

export function appendSessionMessage(sessionId: string, message: string) {
  return requestJson(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function getTask(taskId: string) {
  return requestJson(`/api/tasks/${encodeURIComponent(taskId)}`);
}

export function getTaskTrace(taskId: string) {
  return requestJson(`/api/tasks/${encodeURIComponent(taskId)}/trace`);
}

export function openSessionEvents(sessionId: string, afterSeq = 0) {
  return new EventSource(
    `${baseUrl}/api/sessions/${encodeURIComponent(sessionId)}/events?after_seq=${afterSeq}`,
  );
}
```
