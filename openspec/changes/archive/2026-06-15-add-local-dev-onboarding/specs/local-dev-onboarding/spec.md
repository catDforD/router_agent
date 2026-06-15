## ADDED Requirements

### Requirement: Local environment template is provided
The repository SHALL provide a committed example environment file for local backend development.

#### Scenario: Developer copies environment template
- **WHEN** a developer copies `.env.example` to `.env`
- **THEN** the resulting environment contains local-safe defaults for app environment, database URL, artifact root, MCP mode, and log level

### Requirement: Local PostgreSQL setup is documented
The repository SHALL document repeatable local PostgreSQL setup options for backend development.

#### Scenario: Docker Compose setup path
- **WHEN** a developer has Docker available
- **THEN** the documentation provides commands to start a PostgreSQL service whose credentials and database match the default backend `DATABASE_URL`

#### Scenario: WSL manual setup path
- **WHEN** Docker is unavailable or image pulls fail
- **THEN** the documentation provides PostgreSQL installation, service start, user creation, database creation, and connection verification commands for WSL/Debian

### Requirement: Artifact store verification is documented and scripted
The repository SHALL provide documented and scripted commands for running migrations, creating representative artifact content and metadata, and verifying artifact APIs.

#### Scenario: Setup script prepares local database state
- **WHEN** PostgreSQL is reachable and a developer runs the setup helper script
- **THEN** dependencies are synchronized, migrations are applied, the local artifact directory is created, and representative artifact content and metadata are created

#### Scenario: Developer verifies artifact APIs
- **WHEN** the backend API is running after setup
- **THEN** the documentation provides curl commands for task artifact listing and artifact content retrieval

### Requirement: Local runtime outputs are ignored
The repository SHALL keep local secrets and runtime artifact content out of version control.

#### Scenario: Local runtime files are generated
- **WHEN** setup creates `.env` or files under `data/`
- **THEN** those files are ignored by Git while `.env.example` remains tracked
