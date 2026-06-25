## 当前后端实现思路梳理

### 当前 agent loop 工具集

当前真正暴露给默认 Main Agent 的是 generic Codex-like 工具集，来自 `GENERIC_MAIN_AGENT_TOOL_REGISTRY`：

1. `list_files`：列出 workspace 内文件或目录。
   - 参数：`task_id`、`path`、`recursive`、`max_entries`
2. `read_file`：读取 workspace 内文本文件，带最大字符数限制。
   - 参数：`task_id`、`path`、`max_chars`
3. `write_file`：向 workspace 内写入 UTF-8 文本。
   - 参数：`task_id`、`path`、`content`、`create_dirs`
4. `apply_patch`：在 workspace 内应用 unified patch。
   - 参数：`task_id`、`patch`、`cwd`
5. `exec_command`：在 workspace 内执行 shell 命令并返回截断后的输出。
   - 参数：`task_id`、`command`、`cwd`、`timeout_seconds`
6. `git_status`：读取当前 workspace 的 git 分支和 short status。
   - 参数：`task_id`、`cwd`
7. `read_artifact`：读取 Router artifact 的元数据或受限正文。
   - 参数：`task_id`、`artifact_id`、`mode`、`max_chars`
8. `write_artifact`：写入持久化 artifact，供后续 replay、审计或前端展示使用。
   - 参数：`task_id`、`name`、`content`、`summary`、`artifact_type`、`mime_type`
9. `plc_dev`：生成或更新 PLC 实现产物，并可直接控制开发 worker 参数。
   - 参数：`task_id`、`objective`、`rationale_summary`、`target_language`、`template`、`language_hint`、`enable_socratic_spec`、`socratic_skip`、`compiler_type`、`rpc_pipeline`、`llm`
10. `plc_test`：基于当前需求和代码运行测试 worker。
   - 参数：`task_id`、`objective`、`rationale_summary`、`fuzz_method`、`case_count`、`enable_fuzz_test`、`llm`
11. `plc_formal`：基于当前需求和代码运行形式化验证 worker。
   - 参数：`task_id`、`objective`、`rationale_summary`、`compiler_type`、`properties`、`natural_language_requirements`、`llm`
12. `plc_repair`：基于当前代码和失败证据运行修复 worker。
   - 参数：`task_id`、`objective`、`rationale_summary`、`repair_source`、`repair_targets`、`repair_failure_notes`、`compiler_type`、`llm`

`call_mcp_tool` 已不再作为 Main Agent service/function 或默认工具入口保留。底层 MCP server 工具名仍是 `plc_dev.run`、`plc_test.run`、`plc_formal.run`、`plc_repair.run`，但 Main Agent 通过上面的 4 个直接工具调度 worker。`finish_task` 不暴露给模型，最终完成由“无 tool call 的自然语言回复 + StopPolicy”驱动。

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


### 当前控制外部 agent 的 MCP 工具细粒度如何？4 个工具具体能控制哪些参数？

当前 Main Agent 直接暴露 4 个 PLC worker 工具：

- `plc_dev`：生成或更新 PLC 实现产物。
- `plc_test`：基于当前需求和代码运行测试 worker。
- `plc_formal`：基于当前需求和代码运行形式化验证 worker。
- `plc_repair`：基于当前代码和失败证据运行修复 worker。

每个工具都能传入通用参数：

- `task_id`：目标任务。
- `objective`：给对应 worker 的自然语言目标说明。
- `rationale_summary`：记录本次调用原因，进入 tool-call 观测事件。
- `llm`：可选 LLM 配置对象，字段包括 `model`、`base_url`、`temperature`、`timeout_seconds`、`max_retries`。

各工具额外控制字段如下：

- `plc-dev` 可用：`target_language`、`template`、`language_hint`、`enable_socratic_spec`、`socratic_skip`、`compiler_type`、`rpc_pipeline`、`llm`
- `plc-test` 可用：`fuzz_method`、`case_count`、`enable_fuzz_test`、`llm`
- `plc-formal` 可用：`compiler_type`、`properties`、`natural_language_requirements`、`llm`
- `plc-repair` 可用：`repair_source`、`repair_targets`、`repair_failure_notes`、`compiler_type`、`llm`

后端会把这些直接参数组装为 `WorkerInput.worker_config`。`worker_input_builder` 会为不同 worker 自动生成默认 `worker_config`，调用方显式传入的配置会覆盖默认值；`WorkerInput` 校验会拒绝该 worker 不支持的非空字段。
