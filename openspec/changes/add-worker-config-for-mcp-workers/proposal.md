## Why

Main Agent needs a formal way to pass worker execution knobs to the PLC worker boundary so development, testing, formal verification, and repair can be configured directly without overloading `WorkerContext`.

## What Changes

- Add a structured `worker_config` field to Router v1 `WorkerInput`.
- Keep `WorkerContext` focused on task semantics and move worker execution knobs into `worker_config`.
- Allow `call_plc_dev`, `call_plc_test`, `call_plc_formal`, `call_plc_repair`, and `run_parallel_workers` to receive an optional worker config that flows into `WorkerInput`.
- Preserve existing tool names, MCP transport, and default behavior.
- Keep the simulated/mock MCP worker aligned with the same config contract so local integration tests can use the same input surface as future real workers.

## Impact

- Affected code:
  - `backend/app/models/router_schema.py`
  - `backend/app/workers/worker_input_builder.py`
  - `backend/app/agents/tools.py`
  - `backend/app/mcp/llm_worker.py`
  - `backend/app/mcp/mock_worker.py`
  - `backend/app/tests/unit/`
  - `schema/worker_input.schema.json`
  - `schema/ts/router_contract.d.ts`
- Public Router v1 behavior remains compatible because the new config is optional.
- Existing mock and real MCP routes continue to use the same tool names and payload envelope.
