## Context

The backend now has real PostgreSQL persistence, Alembic migrations, and a local Artifact Store. A developer with a fresh clone needs to know how to provision PostgreSQL, configure environment variables, run migrations, create representative artifacts, start the API, and verify the result.

The repository currently has development scripts, but no README, `.env.example`, Docker Compose file, or local setup guide.

## Goals / Non-Goals

**Goals:**

- Make Docker Compose the shortest documented path for local PostgreSQL.
- Keep a WSL/manual PostgreSQL fallback path for environments where Docker Hub or Docker Desktop is unavailable.
- Provide a committed environment template that matches application defaults.
- Provide a small script for the common setup path after PostgreSQL is available.
- Document verification commands for artifact metadata and content APIs.

**Non-Goals:**

- Change application settings behavior.
- Add production deployment guidance.
- Add secret management beyond local `.env` hygiene.
- Make Docker required for all developers.

## Decisions

1. Use `docker-compose.yml` for the primary local database path.

   The compose service uses `postgres:17` and the same credentials already used by `Settings.database_url`: user `router`, password `router`, database `router`, port `5432`.

   Alternative considered: require developers to install PostgreSQL directly. That is useful as a fallback but creates more OS-specific setup work.

2. Commit `.env.example`, not `.env`.

   `.env.example` documents local defaults without storing secrets or machine-specific paths. `.env` remains ignored.

3. Keep setup scripting intentionally small.

   `scripts/dev_setup_db.sh` should create the local artifact root, run `uv sync`, apply migrations, and create representative artifacts. It should assume PostgreSQL is already reachable rather than trying to install Docker or system packages.

4. Document reset and troubleshooting.

   The local guide should include Docker reset commands, WSL service commands, database creation commands, and API curl checks so developers can recover from common setup failures.

## Risks / Trade-offs

- [Risk] Docker Hub may be unavailable in some networks. -> Mitigation: document WSL/manual PostgreSQL fallback.
- [Risk] Port `5432` may already be in use. -> Mitigation: document checking/stopping the conflicting service or changing `DATABASE_URL` and compose port mapping.
- [Risk] Setup scripts can hide prerequisites. -> Mitigation: keep the script narrow and document the prerequisite that PostgreSQL must already be reachable.
