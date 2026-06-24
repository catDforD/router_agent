## 1. Contract

- [x] 1.1 Add `WorkerConfig` and `WorkerLLMConfig` to the Router v1 model surface.
- [x] 1.2 Attach `worker_config` to `WorkerInput`.
- [x] 1.3 Keep worker-specific validation strict while allowing persisted null fields to round-trip.

## 2. Tooling

- [x] 2.1 Extend `call_plc_dev`, `call_plc_test`, `call_plc_formal`, `call_plc_repair`, and `run_parallel_workers` with optional worker config input.
- [x] 2.2 Merge task defaults with explicit overrides in the worker input builder.
- [x] 2.3 Preserve existing tool names and mock/real routing.

## 3. Worker Simulation

- [x] 3.1 Pass worker config through the LLM-backed MCP worker request path.
- [x] 3.2 Align mock worker prompt/metadata behavior with the same config contract.
- [x] 3.3 Keep artifact metadata within the artifact metadata schema.

## 4. Docs and Verification

- [x] 4.1 Export schema updates to JSON Schema and TypeScript declaration files.
- [x] 4.2 Add a local note mapping the old API doc context fields to `worker_config`.
- [x] 4.3 Add regression tests for worker config parsing, propagation, and mock worker success paths.
- [x] 4.4 Run focused tests, `compileall`, and `git diff --check`.
