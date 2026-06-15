## 1. Local Configuration

- [x] 1.1 Add `.env.example` with local backend defaults.
- [x] 1.2 Add `docker-compose.yml` with a PostgreSQL service matching the default `DATABASE_URL`.
- [x] 1.3 Ensure local runtime data and secrets are ignored while `.env.example` remains trackable.

## 2. Developer Documentation

- [x] 2.1 Add `docs/local-dev.md` with Docker Compose quickstart.
- [x] 2.2 Document WSL/manual PostgreSQL fallback setup.
- [x] 2.3 Document migrations, artifact creation, API startup, curl verification, reset, and troubleshooting commands.

## 3. Setup Helper

- [x] 3.1 Add `scripts/dev_setup_db.sh` to create the artifact root, sync dependencies, apply migrations, and create representative artifacts.
- [x] 3.2 Make the setup script fail fast and print the verification commands a developer should run next.

## 4. Validation

- [x] 4.1 Verify YAML and shell script syntax with available local tools.
- [x] 4.2 Run `git diff --check`.
- [x] 4.3 Confirm OpenSpec apply progress is complete.
