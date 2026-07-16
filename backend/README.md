# WheelMatch backend

Phase 0 provides the production-shaped FastAPI foundation only. Product modules are introduced
in later phases from `docs/implementation-roadmap.md`.

## Local setup

1. Copy `.env.example` to `.env` and replace local-only placeholders.
2. Create a virtual environment and install the locked dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --require-hashes -r backend\requirements-dev.lock
.\.venv\Scripts\python -m pip install --no-deps -e backend
```

3. Start Docker Desktop, then start dependencies and the API:

```powershell
docker compose --env-file .env up --build api worker outbox-relay
```

The API depends on a one-shot migration job; migrations apply before API and worker startup.

## Runtime entry points

```powershell
.\.venv\Scripts\uvicorn app.main:app --app-dir backend --reload
.\.venv\Scripts\python -m app.workers.outbox_relay
.\.venv\Scripts\python -m app.workers.main
```

## Validation

```powershell
.\.venv\Scripts\ruff format --check backend
.\.venv\Scripts\ruff check backend
.\.venv\Scripts\mypy --config-file backend\pyproject.toml backend
.\.venv\Scripts\pytest backend\tests\unit
powershell -File backend\scripts\run-integration.ps1 -EnvFile .env
.\.venv\Scripts\python backend\scripts\export_openapi.py --check
docker compose --env-file .env config --quiet
docker build -f backend\Dockerfile backend
```

Integration tests require `WHEELMATCH_TEST_DATABASE_URL` and
`WHEELMATCH_TEST_REDIS_URL`. They skip rather than silently using SQLite.

The runtime and development locks are generated from `pyproject.toml`; do not hand-edit them:

```powershell
python -m pip install "pip-tools>=7.5,<8"
python -m piptools compile --generate-hashes --output-file backend\requirements.lock backend\pyproject.toml
python -m piptools compile --extra dev --generate-hashes --output-file backend\requirements-dev.lock backend\pyproject.toml
```

## Local infrastructure strategy

- PostgreSQL/PostGIS and Redis run through Docker Compose.
- LocalStack provides local SQS and S3-compatible AWS APIs.
- Production uses managed RDS/PostGIS, ElastiCache, SQS, S3, Secrets Manager, and KMS.
