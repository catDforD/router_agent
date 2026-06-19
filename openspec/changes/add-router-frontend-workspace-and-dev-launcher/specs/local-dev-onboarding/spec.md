## ADDED Requirements

### Requirement: One-command development launcher is provided
The repository SHALL provide a root local development launcher that can be invoked with a command shaped like `uv run main.py` to run the Router development stack.

#### Scenario: Launcher starts core frontend and backend processes
- **WHEN** a developer runs `uv run main.py` from the repository root with local dependencies available
- **THEN** the launcher starts the backend API process and frontend development server process
- **AND** it keeps both processes attached to the terminal with identifiable log prefixes

#### Scenario: Launcher prepares required runtime directories
- **WHEN** the launcher starts
- **THEN** it ensures the configured artifact root directory exists before starting the backend API

#### Scenario: Launcher applies database migrations by default
- **WHEN** the launcher verifies the configured database is reachable
- **THEN** it applies current Alembic migrations before reporting the backend API as ready unless migration execution is explicitly disabled

### Requirement: Launcher handles supporting services
The local development launcher SHALL start or verify supporting services needed by the configured local Router runtime.

#### Scenario: PostgreSQL service can be started through Docker Compose
- **WHEN** the developer requests managed PostgreSQL startup or uses the launcher default that manages PostgreSQL
- **THEN** the launcher starts the existing Docker Compose `postgres` service and waits until it is ready before running migrations

#### Scenario: Externally managed PostgreSQL can be used
- **WHEN** the developer disables managed PostgreSQL startup
- **THEN** the launcher verifies the configured `DATABASE_URL` is reachable and fails with an actionable message if it is not reachable

#### Scenario: Local PLC worker MCP server starts when required
- **WHEN** the effective worker configuration requires a real local PLC worker MCP server or the developer explicitly requests worker startup
- **THEN** the launcher starts `scripts/start_plc_worker_mcp_server.py` and includes the MCP server URL in the startup summary

#### Scenario: Mock worker mode does not require MCP worker startup
- **WHEN** the effective worker configuration uses only mock worker execution
- **THEN** the launcher does not require a PLC worker MCP server before reporting the stack as ready

### Requirement: Launcher prints process and endpoint summary
The local development launcher SHALL print a concise startup summary that identifies running processes and useful access URLs.

#### Scenario: Process table is printed
- **WHEN** all required launcher-managed processes have started
- **THEN** the terminal output includes a table with process name, PID when available, port or service identifier, status, and command

#### Scenario: Access URLs are printed
- **WHEN** the frontend and backend are ready
- **THEN** the terminal output includes the frontend URL, backend base URL, backend health URL, OpenAPI docs URL, task API base URL, task SSE URL pattern, and MCP worker URL when the worker server is running

#### Scenario: Startup failure reports attempted processes
- **WHEN** one required process or readiness check fails during startup
- **THEN** the launcher reports which process or check failed, shows already started process information, and shuts down launcher-managed child processes that should not remain running

### Requirement: Launcher manages shutdown coherently
The local development launcher SHALL handle user interruption and child process exits predictably.

#### Scenario: Ctrl-C stops launcher-managed child processes
- **WHEN** the developer sends an interrupt to the launcher
- **THEN** the launcher terminates frontend, backend, and worker child processes it started and prints a shutdown summary

#### Scenario: Persistent Docker PostgreSQL is not destroyed by default
- **WHEN** the launcher started PostgreSQL through Docker Compose and then shuts down
- **THEN** it does not remove the PostgreSQL container volume or reset database state unless the developer explicitly requests cleanup behavior

#### Scenario: Child process exit is reported
- **WHEN** a required child process exits unexpectedly
- **THEN** the launcher reports the process name and exit status and begins coordinated shutdown of the remaining launcher-managed runtime processes

### Requirement: Launcher usage is documented
The repository SHALL document the one-command launcher alongside the existing manual local development setup.

#### Scenario: Documentation includes common launch path
- **WHEN** a developer reads the local development documentation
- **THEN** it describes running `uv run main.py`, required prerequisites, expected process summary, expected access URLs, and shutdown behavior

#### Scenario: Documentation preserves manual setup path
- **WHEN** a developer cannot or does not want to use the launcher
- **THEN** the documentation still provides the manual PostgreSQL, migration, backend, worker, and frontend commands needed to run the system
