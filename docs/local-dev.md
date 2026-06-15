# Local Development Setup

This guide brings up the Router backend with PostgreSQL and the local Artifact Store.

## Prerequisites

- Python managed by `uv`
- Docker with Docker Compose, or local PostgreSQL
- A shell from the repository root

## Environment

Create a local environment file:

```bash
cp .env.example .env
```

The setup script loads `.env` automatically. For manual commands in your current shell, load it with:

```bash
set -a
source .env
set +a
```

The default local database URL is:

```text
postgresql+psycopg://router:router@localhost:5432/router
```

Local artifact content is written under:

```text
data/artifacts/
```

Both `.env` and `data/` are ignored by Git.

## Option A: Docker Compose PostgreSQL

Start PostgreSQL:

```bash
docker compose up -d postgres
```

Check readiness:

```bash
docker compose ps
docker compose exec postgres pg_isready -U router -d router
```

If Docker Hub image pulls fail in your network, use the WSL/manual setup below.

## Option B: Linux PostgreSQL

Install and start PostgreSQL:

```bash
sudo apt update
sudo apt install -y postgresql postgresql-client
sudo service postgresql start
```

Create or reset the local user/database:

```bash
sudo -u postgres psql <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'router') THEN
    CREATE ROLE router LOGIN PASSWORD 'router';
  ELSE
    ALTER ROLE router WITH LOGIN PASSWORD 'router';
  END IF;
END
$$;
SQL

sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname = 'router'" | grep -q 1 || sudo -u postgres createdb -O router router
sudo -u postgres psql -c "ALTER DATABASE router OWNER TO router;"
```

Verify the project connection string:

```bash
psql 'postgresql://router:router@localhost:5432/router' -c 'select current_user, current_database();'
```

Expected user/database:

```text
router | router
```

## Apply Migrations and Create Artifacts

Run the setup helper after PostgreSQL is reachable:

```bash
bash scripts/dev_setup_db.sh
```

Or run the steps manually:

```bash
mkdir -p data/artifacts
uv sync
uv run alembic upgrade head
uv run python scripts/dev_create_artifacts.py
```

The artifact script prints generated artifact IDs and example curl commands.

## Start the API

```bash
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

## Verify Artifact APIs

List artifacts for the seeded task:

```bash
curl http://127.0.0.1:8000/api/tasks/task-001/artifacts
```

Read a concrete artifact by replacing `<artifact_id>` with one printed by `scripts/dev_create_artifacts.py`:

```bash
curl http://127.0.0.1:8000/api/artifacts/<artifact_id>
```

Expected behavior:

- `GET /api/tasks/task-001/artifacts` returns artifact metadata and no embedded content.
- `GET /api/artifacts/<artifact_id>` returns metadata plus UTF-8 text content.
- Unknown artifact IDs return `404`.

## Inspect PostgreSQL

```bash
psql 'postgresql://router:router@localhost:5432/router' -c "select id, task_id, type, version, uri, content_hash from artifacts order by created_at, version;"
```

## Inspect Local Artifact Files

```bash
find data/artifacts -maxdepth 5 -type f -print
```

## Reset Local State

Docker Compose database reset:

```bash
docker compose down -v
docker compose up -d postgres
uv run alembic upgrade head
uv run python scripts/dev_create_artifacts.py
```

Local artifact files reset:

```bash
rm -rf data/artifacts
mkdir -p data/artifacts
```

WSL PostgreSQL service restart:

```bash
sudo service postgresql restart
pg_isready
```

## Troubleshooting

### Port 5432 Is Already In Use

Check what is listening:

```bash
ss -ltnp | grep 5432 || true
```

Stop the conflicting local PostgreSQL service or change both `DATABASE_URL` and the Docker Compose port mapping.

### Docker Desktop Is Not Available From WSL

Enable Docker Desktop WSL integration for your distro, or use the WSL/manual PostgreSQL setup.

### Docker Hub Pulls Fail

Use the WSL/manual PostgreSQL setup. The backend only needs a PostgreSQL server reachable at `DATABASE_URL`; Docker is not required.

### Password Authentication Fails For `router`

Re-run the WSL user/database creation commands from this document, then verify:

```bash
psql 'postgresql://router:router@localhost:5432/router' -c 'select current_user, current_database();'
```
