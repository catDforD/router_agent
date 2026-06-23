# Agent API Smoke Test

这份说明用于通过已经启动的后端 HTTP API 验证 Codex-like 后端 Agent。

## 前置条件

先完成数据库初始化和迁移：

```bash
bash scripts/dev_setup_db.sh
```

启动后端时需要显式允许本地执行工具，否则 `list_files`、`write_file`、
`exec_command` 等工具会被 policy 拒绝：

```bash
AGENT_EXECUTION_MODE=local_full_access \
MAIN_AGENT_PROVIDER=openai_compatible \
MAIN_AGENT_MODEL=<your-chat-completions-model> \
MAIN_AGENT_API_KEY=<your-api-key> \
MCP_MODE=mock \
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

如果只测试 `call_mcp_tool` 的 mock PLC worker，可以保留 `MCP_MODE=mock`。
如果要连真实 PLC worker MCP server，再改为 `MCP_MODE=real` 或 `hybrid`，并按
本地开发文档启动 worker 服务。

## 运行脚本

查看内置测试场景：

```bash
uv run python scripts/api_agent_plc_smoke.py --list-cases
```

只生成 workspace 和请求 payload，不提交 API：

```bash
uv run python scripts/api_agent_plc_smoke.py \
  --case local_motor_start_stop \
  --prepare-only
```

提交全部场景并等待任务结束：

```bash
uv run python scripts/api_agent_plc_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --timeout 240
```

只跑某一个调试场景：

```bash
uv run python scripts/api_agent_plc_smoke.py \
  --case debug_estop_latch \
  --show-final-report
```

脚本默认会在 `data/api-smoke-workspaces/<run-id>/` 下创建每个任务的
workspace，并把绝对路径写入 `project_context.workspace_root`。后端进程必须能
访问这个路径。

## 场景覆盖

- `local_motor_start_stop`：空 workspace，要求 Agent 创建 ST 电机启停逻辑并运行
  `python tests/check_contract.py`。
- `debug_estop_latch`：已有 ST 文件缺少急停安全处理，要求 Agent 修复并运行验证。
- `debug_timer_fault_reset`：已有泵故障定时和复位逻辑错误，要求 Agent 修复并运行
  验证。
- `mcp_worker_dev_test`：要求 Agent 显式使用 `call_mcp_tool` 调用
  `plc_dev.run` 和 `plc_test.run`，用于验证 domain tool 分发和 worker 事件。

## 观察结果

脚本会打印：

- `task_id`
- 最终 `status` / `phase`
- `events_url`
- workspace 路径
- trace 中的事件类型
- worker 类型
- artifact 类型

也可以手动打开 SSE：

```bash
curl -N http://127.0.0.1:8000/api/tasks/<task_id>/events
```

或读取 trace 和 artifacts：

```bash
curl http://127.0.0.1:8000/api/tasks/<task_id>/trace
curl http://127.0.0.1:8000/api/tasks/<task_id>/artifacts
curl http://127.0.0.1:8000/api/artifacts/<artifact_id>
```

## 判断标准

本地执行类场景通常应看到：

- `agent.started`
- `agent.message`
- `agent.tool_called`
- `agent.tool_result`
- `agent.completed`
- `task.succeeded`

`mcp_worker_dev_test` 还应看到：

- `worker.started`
- `artifact.created`
- `worker.completed`

如果任务失败，先看 `/api/tasks/<task_id>/trace` 中最后几个事件。常见原因：

- `AGENT_EXECUTION_MODE` 没有设为 `local_full_access`
- `MAIN_AGENT_MODEL` 或 API key 未配置
- 模型没有按提示调用工具
- `MCP_MODE=real` 时 worker MCP server 未启动
