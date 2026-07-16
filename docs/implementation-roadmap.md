# Implementation roadmap

## Delivery principles

- Build vertical, production-shaped slices in the modular monolith.
- Land database constraints and authorization tests with each feature.
- Keep Android as the release target while running platform-independent Flutter tests.
- Do not begin a phase until its data and security dependencies are stable.
- Use feature flags for incomplete user-facing paths.
- Treat commands as verified only where an implemented manifest or README is linked.

## Dependency graph

```mermaid
flowchart LR
    P0["0. Foundations"] --> P1["1. Identity and authorization"]
    P1 --> P2["2. Catalogue, listings, media, location"]
    P2 --> P3["3. Verification, moderation, publication"]
    P2 --> P4["4. Discovery and preferences"]
    P3 --> P4
    P1 --> P5["5. Interest and match"]
    P3 --> P5
    P5 --> P6["6. Messaging and notifications"]
    P1 --> P7["7. Dealer and admin operations"]
    P3 --> P7
    P6 --> P7
    P4 --> P8["8. Hardening and Android launch"]
    P6 --> P8
    P7 --> P8
    P8 --> P9["9. iOS release readiness"]
```

## Phase 0 — foundations

**Dependencies:** none.

**Status (2026-07-16):** Backend foundation complete and validated. The Flutter scaffold and mobile CI are not started, so the combined cross-platform phase remains open.

**Deliverables**

- Backend and Flutter scaffolds with committed dependency locks.
- Docker development environment for PostgreSQL/PostGIS and Redis; local SQS/S3 strategy selected.
- Configuration validation, Secrets Manager interface, structured logging, Sentry, trace IDs.
- Alembic baseline, health/readiness endpoints, CI, container build.
- Transactional outbox, idempotency primitive, worker envelope, problem response.
- API schema generation/client strategy.

**Acceptance criteria**

- Empty migration applies to real PostgreSQL/PostGIS.
- API, worker, and mobile tests run in CI.
- No secret values in repository or telemetry.
- Duplicate synthetic event is processed once.
- Container runs non-root and exposes correct health signals.

**Verified backend validation**

```powershell
.\.venv\Scripts\ruff format --check backend
.\.venv\Scripts\ruff check backend
.\.venv\Scripts\mypy --config-file backend\pyproject.toml backend
.\.venv\Scripts\pytest backend\tests\unit
powershell -File backend\scripts\run-integration.ps1 -EnvFile .env
.\.venv\Scripts\python backend\scripts\export_openapi.py --check
docker compose --env-file .env config --quiet
docker compose --env-file .env run --rm migrate
```

Backend evidence: real PostGIS migration and metadata drift check passed; 26 tests passed with 83.71% branch-aware coverage; duplicate SQS delivery produced one consumer marker; API readiness passed; the image ran as UID/GID 10001. Flutter acceptance criteria remain pending.

## Phase 1 — identity and authorization

**Dependencies:** Phase 0.

**Deliverables**

- Registration, email/phone verification, login, proposed rotating sessions, recovery.
- User/profile/seller-profile state.
- Dealer organizations, membership lifecycle, centralized permission policy.
- Mobile auth bootstrap, secure storage adapter, route guards.
- Audit framework and permission cache invalidation.

**Acceptance criteria**

- No registration account-type selection.
- Suspended account loses personal and dealer capability immediately.
- Leaving dealer preserves personal resources but revokes organization access.
- Owner/admin/member role matrix has object-level tests.
- Refresh replay revokes the session family if proposed session ADR is accepted.

## Phase 2 — catalogue, listings, media, and location

**Dependencies:** Phases 0–1.

**Deliverables**

- Controlled vehicle taxonomy and canonical identity foundation.
- Private listing drafts with exactly-one owner constraint.
- Personal/dealer operating-context selector.
- Presigned S3 upload, quarantine, processing worker, derivatives.
- PostGIS location write/filter and privacy-safe response mapper.
- Draft/listing mobile editor and resumability.

**Acceptance criteria**

- Invalid owner combinations fail in PostgreSQL.
- Direct object upload cannot cross listing ownership.
- Sanitized derivatives contain no EXIF/GPS.
- Personal listing contract contains no exact coordinate/address/cell.
- Dealer public pin requires current verified organization address.

## Phase 3 — verification, moderation, publication

**Dependencies:** Phase 2.

**Deliverables**

- Identity provider adapter and append-only attempts.
- Canonical vehicle HMAC identity and owner–vehicle verification.
- 180-day reusable verification policy and listing ownership checks.
- Moderation cases/decisions and admin review UI/API.
- Publication readiness and transactional publish/relist.
- Revocation/expiry propagation workers and cache invalidation.

**Acceptance criteria**

- Pending/failed verification cannot produce a public URL or discovery result.
- Reused verification creates a new listing-level check without extending expiry.
- Two concurrent publications for one vehicle produce one live listing.
- Revocation immediately removes public eligibility and eventually marks all dependents suspended.
- Material listing edit invalidates prior moderation.

## Phase 4 — discovery and preferences

**Dependencies:** Phases 2–3.

**Deliverables**

- Reactions, reaction history, saved listings, explicit preferences, hidden sellers.
- PostGIS/full-text candidate generation.
- Deterministic ranker-v1 and diversification.
- Redis feed sessions, opaque cursors, impressions, repeat prevention.
- SQS inferred-profile recalculation and reset generation.
- Flutter swipe feed and preference-management screens.

**Acceptance criteria**

- Not Interested disappears immediately, including from prefetched pages.
- Reaction retry is idempotent and no duplicate row exists.
- Neutral does not affect saved/request/match/chat records.
- Explicit filters override inferred features.
- Redis outage preserves correct exclusions through PostgreSQL fallback.
- Cursor reuse with changed filters/location fails safely.

## Phase 5 — interest and match

**Dependencies:** Phases 1 and 3.

**Deliverables**

- Pair-level request thread and two-attempt lifecycle.
- Eligibility, create, reject, withdraw, expiry, accept APIs.
- 24-hour withdrawn and 14-day expired retry cooldowns.
- Unique match/conversation creation on acceptance.
- Mobile confirmation and terminal/cooldown states.

**Acceptance criteria**

- Required partial pending index exists.
- Concurrent create/accept/withdraw/expire tests preserve one valid transition.
- Rejection permanently closes pair.
- Attempt 2 is always final.
- Block/sold/removed closes contact without disclosing block.
- Match and conversation are created in the acceptance transaction.

**Assumption gate:** confirm or replace the proposed seven-day pending expiry before enabling production scheduler.

## Phase 6 — messaging and notifications

**Dependencies:** Phase 5.

**Deliverables**

- Persistent text messages, system events, receipts, history cursors.
- WebSocket ticketing, per-subscription authorization, Redis fan-out, REST recovery.
- Blocks, message reports, spam/rate controls.
- In-app notifications, device tokens, FCM worker, preferences.
- Mobile conversation/reconnect/push navigation.

**Acceptance criteria**

- Message acknowledgement occurs after commit.
- Duplicate client_message_id produces one message.
- Reconnect restores missed history.
- Unauthorized conversation subscription/read/send is denied.
- Push failure does not lose message.
- Private content is absent from default push and logs.

## Phase 7 — dealer assignment, moderation operations, and n8n

**Dependencies:** Phases 3 and 6.

**Deliverables**

- Initial dealer assignment on acceptance.
- Metadata-only organization inbox.
- Assign/reassign/takeover transaction and neutral buyer event.
- Membership-loss unassignment and socket revocation.
- Purpose-limited moderation access.
- Draft n8n workflows for approved asynchronous automations using scoped callbacks.

**Acceptance criteria**

- One active assignment partial index exists.
- Owners/admins cannot read messages before takeover.
- Previous agent loses REST and WebSocket access immediately.
- Unassigned conversations preserve history but prevent seller replies.
- n8n has no production table access and no critical state transition.
- Production workflow activation/execution remains separately approved.

## Phase 8 — security, resilience, and Android launch

**Dependencies:** Phases 4, 6, and 7.

**Deliverables**

- Performance/load tests, query/index review, queue and WebSocket sizing.
- WAF/rate-limit policies, external security assessment, privacy review.
- Backup/PITR and restore drill; DLQ replay drill.
- SLO dashboards, synthetic journeys, operational runbooks.
- Android signing, store privacy/data-safety declarations, staged rollout.

**Acceptance criteria**

- Critical SLO assumptions validated or revised from load evidence.
- No high/critical security findings open.
- Restore meets approved RPO/RTO.
- End-to-end verified journey passes in staging.
- Canary rollback and incident response are rehearsed.
- Release telemetry and alerts are live before rollout.

## Phase 9 — iOS readiness

**Dependencies:** stable Android production baseline.

- Configure APNs through FCM, iOS signing, permissions, universal links, privacy manifest, and store review.
- Run iOS device/integration/accessibility tests on macOS CI.
- Resolve platform differences through adapters, not feature forks.

## Recommended implementation order

Within each phase:

1. Migration and database constraints.
2. Domain state/policy tests.
3. Application transaction and repositories.
4. API contract and authorization tests.
5. Worker/outbox behavior.
6. Flutter repository/controller/UI.
7. End-to-end and observability.
8. Feature flag activation.

Stop a phase when its acceptance criteria pass. Do not broaden into future infrastructure or unconfirmed features.
