## Context

Router already builds `WorkerInput` from task state and dispatches PLC worker calls through Main Agent tools. The missing piece is a first-class worker execution config that can travel with the worker input without mixing into `WorkerContext`.

## Goals / Non-Goals

**Goals:**

- Introduce `worker_config` as the execution knob bundle for PLC workers.
- Keep task semantics in `WorkerContext` and execution knobs in `worker_config`.
- Preserve current tool names and transport behavior.
- Keep the mock and LLM-backed worker simulation aligned with the same config contract.

**Non-Goals:**

- Do not rename MCP tools.
- Do not add a new transport layer.
- Do not make any worker config field required.
- Do not introduce a secret-bearing config surface for LLM credentials.

## Decisions

### Use a nested `worker_config` object on `WorkerInput`

The config travels with the worker request envelope so it can be logged, validated, and forwarded consistently across mock and real worker paths.

### Allow only worker-relevant config fields per worker type

`plc-dev`, `plc-test`, `plc-formal`, and `plc-repair` each accept a different subset of config fields. The validator should reject unsupported non-empty fields early so Main Agent mistakes are obvious.

### Preserve defaults in the builder

The worker input builder should continue to provide reasonable defaults for each worker type, then apply explicit overrides from Main Agent.

### Keep metadata bounded

Worker simulation metadata should record the effective config, but artifact metadata must stay within the existing artifact metadata schema.

## Risks / Trade-offs

- [Risk] Merging defaults and overrides can create confusing effective configs. -> Mitigation: keep the final `worker_config` attached to `WorkerInput` and covered by tests.
- [Risk] Optional config fields can become noisy when serialized with nulls. -> Mitigation: treat `null` values as absent for validation of persisted inputs.

