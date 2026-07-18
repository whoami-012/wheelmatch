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

- Concurrent refresh of one token permits one rotation and treats the other as family-revoking replay.
- Concurrent dealer membership changes serialize on current rows and reject stale expected versions.
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

- Registration rejects account-type selection; authentication/profile/session contracts expose no stored hashes.
- Login/recovery failures are generic; verification attempts, expiry, and single use are enforced.
- Access tokens are denied after suspension or session-family revocation; session/profile reads are user-scoped.
- Dealer permission roles, organization suspension, membership removal, audit redaction, and cache invalidation are Phase 1 regression boundaries.
- Personal listing schemas cannot contain coordinates, street address, geohash/cell, or exact distance.
- Unavailable listings remove public location and sensitive content.
- Dealer metadata inbox cannot return message body/preview.
- n8n/analytics event schemas reject precise location, documents, phone/email, or chat.
- Media derivative contains no GPS/EXIF/embedded thumbnail.
- Verification/admin endpoints require purpose and produce audit entry.
- Cursor cannot be reused with changed filters/location.

Backend Phase 2 adds focused coverage for catalogue hierarchy/uniqueness, exactly-one-owner
listing constraints, idempotent audit/outbox draft creation, optimistic versions, dealer
membership loss, signed owner cursors, real PostGIS `ST_DWithin`, coordinate-free location
responses, and real LocalStack quarantine intent/completion/removal. Media scanning, derivative
sanitization, moderation, publication, discovery, mobile, browser, and load tests remain excluded
until their owning phases are implemented.

Backend Phase 3 Slice 1 adds focused unit coverage for orientation, bounded decode, re-encoding,
metadata stripping, version matching, scanner outcomes, and fail-closed configuration. Real
PostgreSQL/LocalStack coverage owns private derivative creation, duplicate/stale delivery,
signature/MIME/checksum rejection, status privacy/IDOR, and atomic finalization. Content
moderation decisions, publication, discovery, mobile, browser, load, n8n, and external-provider
tests remain excluded.

Backend Phase 3 Slice 2 adds focused state-machine/provider/configuration unit coverage. Real
PostgreSQL coverage owns idempotent and concurrent attempt creation, append-only history,
duplicate/conflicting/stale result handling, atomic projection/audit/outbox finalization and
rollback, privacy-safe start/status contracts, and current-user isolation. External provider,
public webhook, document-storage, expiry/revocation propagation, owner–vehicle verification,
moderation, publication, and later-phase tests remain excluded.

Backend Phase 3 Slice 3 adds focused normalization/HMAC, material-fingerprint, ownership-state,
provider, safe-failure, schema-privacy, and fail-closed configuration unit coverage. Real
PostgreSQL coverage owns personal-owner authorization, identity gating, idempotent/concurrent
canonical resolution, append-only history, duplicate/conflicting/stale results, atomic
attempt/audit/outbox finalization and rollback, and privacy-safe start/status contracts. Dealer,
cross-owner, raw-identifier, capture-URL, and provider-evidence leakage are negative assertions.
External providers/webhooks, publication ownership checks, 180-day reuse, revocation propagation,
moderation, publication, discovery, mobile, browser, load, and n8n tests remain excluded.

Backend Phase 3 Slice 4 adds focused unit coverage for every readiness gate, safe-code mapping,
stale listing/media evidence, the response privacy allowlist, and the invariant that all
pre-moderation gates still cannot publish. Real PostgreSQL coverage owns idempotent/versioned
personal submission, cross-owner and dealer boundaries, safe missing-source blockers, one durable
moderation request, duplicate suppression, stale evidence, transaction rollback, and API privacy.
Moderation decisions, publication/relisting/availability, verification reuse, dealer submission,
revocation propagation, LocalStack processing, discovery, mobile, browser/load, n8n, and external
provider tests remain excluded.

Backend Phase 3 Slice 5 adds focused pure-policy coverage for eligibility, provider-versus-policy
effective expiry, immutable source expiry, binding/version mismatches, restricted/revoked/pending/
superseded states, newer conflicts, dealer exclusion, and safe codes. Focused real-PostgreSQL
coverage owns cross-listing reuse, no provider/new-attempt behavior, idempotent and concurrent
duplicate suppression, submission provenance/readiness, provider and policy expiry, source-proof
immutability, identity/vehicle-version invalidation, authorization, and response/audit/outbox
privacy. Publication-time ownership checks, moderation/admin, publication/relisting, dealer
inventory verification, revocation workers, external providers, discovery, mobile, browser/load,
LocalStack media processing, and n8n tests remain excluded.

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

## Implementation and validation workflow

- The main Codex agent exclusively owns production code, migrations, dependencies, tests, OpenAPI, documentation, and validation.
- Implement large phases through bounded vertical slices that deliver one coherent behavior across database, service, authorization, API, audit/outbox, and focused tests.
- Before implementation, define a requirement-to-test impact matrix listing mandatory and explicitly excluded tests.
- Complete each vertical slice before starting the next.
- During each slice, run only targeted Ruff, mypy, and affected tests.
- Test behavior at the lowest reliable layer; do not duplicate the same rule across unit, repository, API, integration, and end-to-end tests.
- Do not run the complete backend suite after every slice or localized fix.
- Run the complete backend acceptance suite once after all slices and targeted checks pass.
- Do not add or run tests for unrelated phases, mobile, unchanged infrastructure, hypothetical behavior, or requirements already proven at the correct layer.
- Add a regression test only when an acceptance requirement lacks coverage or a demonstrated defect would otherwise recur.
- Apply bounded command timeouts and terminate unexpected stalls instead of waiting indefinitely.
- Validation reports must include exact commands, exit codes, concise results, coverage where required, unresolved blockers, and confirmation that no secret values were exposed.
- Distinguish implementation failures from environment or tooling failures before changing source code.
- Stop immediately when the current phase acceptance criteria pass; do not begin the next phase.
