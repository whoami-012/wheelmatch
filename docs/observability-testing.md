# Observability and testing

## Telemetry standard

Use OpenTelemetry-compatible trace context across API, WebSocket, outbox, SQS, worker, and n8n callback boundaries.

Structured events include:

- timestamp, level, service role, environment.
- request, trace, event, execution, and idempotency IDs.
- safe actor/resource identifiers where policy permits.
- operation, outcome code, latency, retry count.
- deployment version and feature/ranker/policy version.

Never log authorization headers, cookies, refresh tokens, upload URLs, documents, vehicle identifiers, exact locations, message bodies, or provider payloads. Sentry applies the same redaction before transport.

## Metrics

### API and database

- Request rate, status, p50/p95/p99 latency by route class.
- PostgreSQL pool wait, transaction duration, lock wait/deadlock, slow query, replica lag.
- Redis latency/error/cache hit and rate-limit decisions.
- Idempotency conflicts and optimistic concurrency failures.

### Product-critical flows

- Feed candidate/ranking latency, empty-feed rate, duplicate/repeat rate.
- Reaction commit and cache-invalidation latency.
- Publication gate duration and failure reason.
- Interest acceptance conflicts and cooldown eligibility.
- Message commit-to-delivery latency, reconnect recovery, unread backlog.
- Unassigned dealer conversation count and oldest waiting age.
- Verification/manual review turnaround and moderation false-positive sampling.

### Async operations

- Outbox oldest unpublished age.
- SQS depth, oldest message, retry, and DLQ count by job.
- Media processing duration/failure.
- FCM delivery outcome and invalid token rate.
- n8n execution/callback failure and provider circuit state.

## Initial objectives

These are launch assumptions pending product confirmation:

| Service indicator | Initial target |
|---|---|
| Core API availability | 99.9% monthly |
| Feed p95 server latency | Under 500 ms |
| Transactional command p95 | Under 400 ms excluding uploads/providers |
| Message commit acknowledgement p95 | Under 300 ms |
| Outbox publication p99 | Under 60 seconds |
| Authorization revocation | Effective immediately at DB; socket propagation within seconds |
| Zero silent loss | Committed messages and outbox events |

Alert on user impact and queue age rather than raw CPU alone.

## Test strategy

| Layer | Coverage |
|---|---|
| Domain unit | State machines, cooldowns, permissions, ranking functions |
| Repository/integration | PostgreSQL constraints, PostGIS, locks, migrations, Redis/SQS adapters |
| API contract | Schema, errors, cursors, idempotency, object authorization |
| Worker | Redelivery, retry classification, DLQ, stale event handling |
| WebSocket | Subscribe/send/read, reconnect, reassignment and membership revocation |
| Mobile unit/widget | Controllers, route states, error mapping, accessibility |
| End-to-end | Register → draft → verify → publish → discover → request → accept → chat |
| Security | IDOR, abuse limits, upload, callback, location inference, secret scanning |
| Resilience | Redis/provider/n8n outage, SQS duplicate, database failover/retry |

Use real PostgreSQL/PostGIS in integration tests. SQLite is insufficient for partial indexes, row locks, geography, constraints, and generated SQL behavior.

## Critical concurrency tests

- Two devices change one reaction with expected_version.
- Duplicate interest create with same/different idempotency keys.
- Concurrent interest accept/withdraw/expire.
- Concurrent attempt-2 creation.
- Concurrent listing publication for same canonical vehicle.
- Verification revocation during publication.
- Concurrent dealer takeover/reassignment.
- Former assignee sends during revocation.
- Duplicate message client IDs and WebSocket redelivery.
- Outbox relay crash before/after broker acknowledgement.

## Contract and privacy tests

- Personal listing schemas cannot contain coordinates, street address, geohash/cell, or exact distance.
- Unavailable listings remove public location and sensitive content.
- Dealer metadata inbox cannot return message body/preview.
- n8n/analytics event schemas reject precise location, documents, phone/email, or chat.
- Media derivative contains no GPS/EXIF/embedded thumbnail.
- Verification/admin endpoints require purpose and produce audit entry.
- Cursor cannot be reused with changed filters/location.

## Validation commands

### Backend

```powershell
.\.venv\Scripts\ruff format --check backend
.\.venv\Scripts\ruff check backend
.\.venv\Scripts\mypy --config-file backend\pyproject.toml backend
.\.venv\Scripts\pytest backend\tests\unit
powershell -File backend\scripts\run-integration.ps1 -EnvFile .env -TestPath backend\tests -Coverage
.\.venv\Scripts\python backend\scripts\export_openapi.py --check
docker compose --env-file .env run --rm migrate
docker compose --env-file .env run --rm migrate alembic -c alembic.ini check
```

Migration CI must also render/review PostgreSQL SQL and run downgrade only where explicitly supported.

### Mobile

```powershell
Set-Location mobile
flutter pub get
dart format --output=none --set-exit-if-changed .
flutter analyze
flutter test
flutter test integration_test
flutter build apk --release
```

### Infrastructure and documentation

```powershell
docker compose --env-file .env config --quiet
mmdc -i <diagram.mmd> -o <diagram.svg>
```

IaC commands are not confirmed until the tool is selected.

## Release validation

- Staging smoke test against real managed dependencies.
- Migration lock/time estimate and rollback plan.
- Synthetic registration, feed, publication, request, accept, chat, and notification flows.
- Restore test evidence current.
- DLQs empty or explicitly accepted.
- Dashboards and alerts deployed before feature activation.
- Security/privacy regression suite passes.
- Mobile release signed, obfuscated as appropriate, and crash-free staging soak completed.

No test claim is valid unless the exact command and environment are reported.
