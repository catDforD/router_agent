#!/usr/bin/env bash
set -euo pipefail

if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://router:router@localhost:5432/router}"
export ARTIFACT_ROOT="${ARTIFACT_ROOT:-./data/artifacts}"

echo "Using DATABASE_URL=${DATABASE_URL}"
echo "Using ARTIFACT_ROOT=${ARTIFACT_ROOT}"

mkdir -p "${ARTIFACT_ROOT}"

echo "Synchronizing Python dependencies..."
uv sync

echo "Checking PostgreSQL connectivity..."
PYTHONPATH=backend uv run python - <<'PY'
from sqlalchemy import create_engine, text

from app.core.config import Settings

settings = Settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
with engine.connect() as connection:
    current = connection.execute(
        text("select current_user, current_database()")
    ).one()
print(f"Connected as {current[0]} to database {current[1]}")
PY

echo "Applying Alembic migrations..."
uv run alembic upgrade head

echo "Creating representative local artifacts..."
uv run python scripts/dev_create_artifacts.py

cat <<'TXT'

Setup complete.

Start the backend:
  uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000

Verify artifact listing:
  curl http://127.0.0.1:8000/api/tasks/task-001/artifacts

Read a specific artifact using an ID printed above:
  curl http://127.0.0.1:8000/api/artifacts/<artifact_id>
TXT
