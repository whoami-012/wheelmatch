# WheelMatch Repository Instructions

## Verified repository state

The checkout contains architecture documentation and the implemented backend Phase 0 foundation. It has no Flutter application or product-domain modules yet. A `.git/` directory exists, but Git does not recognise this checkout as a repository.

## Project purpose and features

WheelMatch is designed as a swipe-based car and bike marketplace. The implemented backend currently provides only platform foundations: health probes, configuration, telemetry, RFC-style errors, PostgreSQL durability primitives, SQS worker infrastructure, and local containers. Product features remain specified in `docs/` and are not implemented.

## Technology stack and dependencies

- Python 3.11, FastAPI, Pydantic Settings, async SQLAlchemy and Alembic.
- PostgreSQL 17 with PostGIS, Redis, SQS/S3 through boto3, structured logging and Sentry.
- Ruff, strict mypy, pytest/pytest-asyncio and coverage for validation.
- Hashed runtime and development locks are committed under `backend/`.
- Docker Compose provides local PostGIS, Redis and LocalStack.

## Architecture and directory structure

- `.codex/skills/n8n-workflow/`: repository-local instructions for n8n MCP work.
- `.agents/`: present but no established skill or instruction convention was found.
- `backend/app/bootstrap/`: FastAPI application factory and lifespan.
- `backend/app/core/`: configuration, database, errors, events, health, idempotency, outbox and telemetry.
- `backend/app/workers/`: SQS consumer and outbox-relay process entry points.
- `backend/migrations/`: Alembic baseline. `backend/tests/`: unit and real-service integration tests.
- `infra/localstack/`: local SQS, DLQ and private S3 bucket initialization.
- `.github/workflows/backend-ci.yml`: backend CI. `docs/`: approved target architecture and roadmap.

## Main entry points

- API: `backend/app/main.py` (`app.main:app`).
- SQS worker: `python -m app.workers.main`.
- Outbox relay: `python -m app.workers.outbox_relay`.
- Migrations: `backend/alembic.ini`.

## Commands

| Task | Verified command |
|---|---|
| Local development | `docker compose --env-file .env up --build api worker outbox-relay` |
| API without containers | `.\.venv\Scripts\uvicorn app.main:app --app-dir backend --reload` |
| Build | `docker compose --env-file .env build api` |
| Format/lint | `.\.venv\Scripts\ruff format --check backend`; `.\.venv\Scripts\ruff check backend` |
| Type check | `.\.venv\Scripts\mypy --config-file backend\pyproject.toml backend` |
| Unit test | `.\.venv\Scripts\pytest backend\tests\unit` |
| Integration test | `powershell -File backend\scripts\run-integration.ps1 -EnvFile .env` |
| Database migration | `docker compose --env-file .env run --rm migrate` |
| OpenAPI drift | `.\.venv\Scripts\python backend\scripts\export_openapi.py --check` |
| Deployment | Not confirmed |

Before running a command, locate it in checked-in documentation, a manifest, or CI configuration. If none exists, report `Not confirmed` rather than guessing.

## Environment variables

`Settings` reads `.env` and `WHEELMATCH_`-prefixed variables. `.env.example` documents local Compose variables and safe placeholders. Verified application variables cover environment/logging, database/Redis URLs, AWS region/endpoint, SQS queue, S3 bucket, Sentry, readiness, pools and worker/outbox polling.

- Never read, print, commit, or copy secret values into code, logs, Markdown, tests, or n8n node parameters.
- Document only variable names that are verified from checked-in configuration.
- Treat local `.env*`, credential exports, and production configuration as approval-gated.

## Database, API, and external services

PostgreSQL/PostGIS is authoritative. Phase 0 migrations create `idempotency_keys`, `outbox_events` and `consumer_events`. Redis is a required readiness dependency. SQS provides redeliverable events with a DLQ; S3 is initialized private in LocalStack. Sentry and AWS Secrets Manager are optional adapters. Only health APIs exist; no product or n8n application integration is implemented.

## Coding and naming conventions

- Use typed async Python and explicit transaction boundaries.
- Keep routers thin and business logic in future domain application services.
- Generate UUIDv7 identifiers and UTC timezone-aware timestamps.
- Use snake_case modules/functions, PascalCase types, stable uppercase error codes and lowercase dotted event types.
- Keep shared infrastructure in `app/core`; do not add product logic there.

- Keep repository-local Codex skills under `.codex/skills/<lowercase-hyphen-name>/SKILL.md` unless a different checked-in convention supersedes it.
- Keep instruction changes concise and avoid restating generic engineering guidance.
- Do not introduce a framework, dependency, directory layout, or naming scheme without an explicit task requiring it.

## Security requirements

Verified controls include production rejection of localhost endpoints, hashed dependency locks, non-root containers, secret-safe configuration summaries, Sentry request redaction, private LocalStack S3, loopback-only host ports and strict event validation.

- Do not expose credentials, tokens, private URLs, personal data, or secret-bearing configuration.
- Do not weaken authentication, authorisation, input validation, audit, or network boundaries if those modules are later added.
- Treat production workflow state and external side effects as separate from repository file changes.

## Error handling and logging

Return `application/problem+json` through `AppError` and stable codes. Unexpected failures emit structured JSON without request bodies or secrets. Preserve correlation IDs and trace context across new boundaries. Do not log event payloads by default.

## Testing and validation

- Run the smallest validation that covers the changed artifact first.
- Backend CI requires Ruff, strict mypy, unit tests, real PostGIS/Redis integration tests, 80% branch-aware coverage, migrations, OpenAPI drift and Alembic metadata drift.
- For instruction-only changes, verify paths, Markdown structure, YAML front matter, and internal consistency.
- Validate repository-local skills with the available skill validator.
- Do not claim application tests, builds, database checks, or deployments passed when no corresponding command is confirmed.
- Broaden validation only when the risk or changed scope justifies it.

## Approval-gated areas

- `.git/` metadata must not be modified.
- Secrets, credential files, environment files, database data, and production infrastructure require explicit approval.
- Authentication, authorisation, migrations, deployment configuration, and externally visible API or webhook contracts require explicit approval if they are later added.
- Production n8n workflows must not be published, activated, disabled, deleted, or executed without explicit approval.

## Definition of done

A change is complete when:

1. The requested scope is implemented without unrelated source changes.
2. Claims about the project are backed by current repository files; unknowns remain labelled `Not confirmed`.
3. Relevant targeted validation passes, with broader validation run only when justified.
4. Secrets and private endpoints are absent from the diff and output.
5. The final report lists changed files, validation performed, unresolved risks, and required manual actions.

## Token-efficient agent workflow

1. Inspect only the documentation, manifests, configuration, and modules relevant to the request.
2. Cache findings during the task; do not repeatedly read unchanged files.
3. Make minimal, scoped changes and preserve unrelated user work.
4. Reuse existing abstractions and configuration before introducing new ones.
5. Run targeted validation before broader tests.
6. Stop when acceptance criteria are met and summarise changes and unresolved risks.

## n8n MCP Usage

- Use the connected n8n MCP server only for workflow-related tasks.
- Search for and inspect relevant existing workflows before creating or modifying one.
- Inspect the workflow ID, active/draft state, triggers, nodes, connections, settings, credential metadata, webhook contracts, and downstream integrations.
- Never expose credential values, tokens, secrets, private URLs, or sensitive execution payloads.
- Do not delete, disable, publish, activate, or execute production workflows without explicit approval.
- Prefer updating an existing workflow over creating a duplicate. Preserve workflow IDs, webhook paths, input/output contracts, and downstream integrations.
- Read the n8n SDK reference and verified node definitions before writing workflow code. Do not assume a node, operation, parameter, trigger, or credential exists.
- Validate individual node configuration before graph changes. Validate branches, fallback paths, retries, timeouts, idempotency, and error handling before saving.
- Keep transactional application operations outside n8n. Use scoped service credentials and controlled APIs instead of direct production database access.
- Clearly distinguish draft changes from the currently published production version in every implementation report.
