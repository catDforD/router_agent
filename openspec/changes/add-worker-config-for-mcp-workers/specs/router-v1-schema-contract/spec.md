## NEW Requirements
### Requirement: Router v1 worker input carries a dedicated worker config
The backend SHALL allow `WorkerInput` to carry an optional `worker_config` object that holds worker execution knobs separate from `WorkerContext`.

#### Scenario: Worker config round-trips through schema and fixtures
- **WHEN** a valid Router v1 `WorkerInput` payload includes `worker_config`
- **THEN** backend validation SHALL accept the payload
- **AND** exported JSON Schema and TypeScript declarations SHALL expose the same optional field

#### Scenario: Persisted worker input can reload with null config fields
- **WHEN** a stored `WorkerInput` payload is reloaded from persistence and its `worker_config` contains null-valued optional fields
- **THEN** backend validation SHALL still accept the payload
- **AND** unsupported non-empty worker config fields SHALL remain rejected

### Requirement: Worker config is worker-specific
The backend SHALL reject non-empty `worker_config` fields that are not supported for the requested worker type.

#### Scenario: Development worker rejects test-only config
- **WHEN** a `plc-dev` `WorkerInput` includes test-only config fields with non-empty values
- **THEN** backend validation SHALL reject the payload before worker dispatch

