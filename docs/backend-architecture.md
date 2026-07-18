# Backend architecture

## Modular monolith

One FastAPI codebase owns business transactions. Runtime processes can scale independently, but modules do not bypass each other through shared table access.

```text
backend/
  app/
    main.py
    bootstrap/
    core/
      config/
      database/
      security/
      telemetry/
      errors/
      idempotency/
      outbox/
    modules/
      identity/
      profiles/
      dealers/
      catalogue/
      listings/
      locations/
      media/
      discovery/
      preferences/
      saved_listings/
      interest_requests/
      matches/
      messaging/
      notifications/
      verification/
      moderation/
      reports/
      admin/
      analytics/
      automation_gateway/
    workers/
    websocket/
  migrations/
  tests/
```

Phase 0 plus backend Phase 1 `identity`, `profiles`, `dealers`, `authorization`, and `audit`
modules are implemented. Backend Phase 2 implements `catalogue`, `listings`, `locations`, and
`media` as private modular-monolith slices. Backend Phase 3 Slices 1–3 implement media processing,
provider-neutral user identity verification, keyed canonical vehicle resolution, and personal
owner–vehicle attempts. Moderation, publication, discovery, WebSockets, and later product workers
remain roadmap targets.

Slice 3 keeps provider session creation between two short transactions. The first locks the active
personal owner, listing/version, current identity projection, and canonical match; the provider
call has no database transaction; the second persists only private provider correlation and safe
state. Provider-result ingestion is an application-service boundary until a production provider
and signature contract are accepted.

## Module contract

Each domain module should contain:

- API schemas and router.
- Application commands/queries.
- Domain models, policies, and state transitions.
- Repository interfaces and SQLAlchemy implementations.
- Events exposed through the outbox.
- Focused tests.

Routers do not contain business logic. Repositories do not authorize. Domain modules do not import another module's persistence internals; they invoke an application-level interface.

## Module responsibilities

| Module | Responsibility |
|---|---|
| Identity | Registration, verification, sessions, suspension |
| Profiles | User and seller profile state |
| Dealers | Organizations, verification, memberships, permissions |
| Authorization | Pure capability/permission policy and versioned Redis projections |
| Audit | Append-only redacted security and lifecycle records |
| Catalogue | Controlled vehicle taxonomies and canonical identity |
| Listings | Drafts, ownership, lifecycle, publication evidence |
| Locations | Private PostGIS writes, search predicates, public mapping |
| Media | Upload intents, object state, sanitization orchestration |
| Discovery | Candidate generation, cursor sessions, ranking |
| Preferences | Reactions, explicit and inferred preferences, hidden sellers |
| Interest requests | Pair thread, attempts, cooldowns, acceptance/rejection/withdrawal |
| Matches | Unique relationship created on accepted request |
| Messaging | Conversations, assignments, messages, receipts, blocks |
| Notifications | In-app records, preferences, FCM delivery intents |
| Verification | Identity and owner–vehicle evidence and reuse |
| Moderation | Listing decisions, cases, fraud holds |
| Reports | User/listing/message reports |
| Admin | Purpose-limited operational commands |
| Automation gateway | Signed n8n events and scoped callbacks |
| Analytics | Sanitized event allowlist and export batches |

## Transaction design

- Use async SQLAlchemy sessions with explicit transaction boundaries in application services.
- Use PostgreSQL constraints as final defence and translate expected violations into stable domain errors.
- Apply SELECT FOR UPDATE in documented lock order for competing transitions.
- Commit idempotency result, domain mutation, audit entry, and outbox event together.
- Avoid network calls inside database transactions. Create provider jobs before or after using a durable state machine.
- Use UTC timestamps and application-generated UUIDv7 identifiers.

Critical lock orders:

- Identity-verification start: user, active/latest attempt, effective projection.
- Identity-verification result: provider reference/attempt, effective projection.

- Refresh: refresh-session row → session family → user; a consumed-token replay revokes the family.
- Dealer membership: actor → organization → actor membership → target membership/user.

- Interest lifecycle: listing → interest_request_thread → latest interest_request.
- Publication: listing → canonical vehicle → owner/account verification → ownership verification → moderation evidence → conflicting listing.
- Dealer reassignment: conversation → active assignment → target membership.
- Message send: conversation/authorization version → active assignment when dealer → message.

## Query design

- Separate public response projections from private persistence models.
- Apply object-level authorization in query services, not after serialization.
- Use keyset/cursor pagination.
- Bound every list query and worker batch.
- Use PostGIS and relational indexes before adding a search cluster.
- Never load verification documents or exact private coordinates into general ORM entities used by listing APIs.

## Runtime roles

| Role | Entry responsibility |
|---|---|
| API | REST, validation, transactions |
| WebSocket | Realtime connections and per-subscription authorization |
| Worker | SQS consumers for media, recommendation, notifications, revocation propagation |
| Outbox relay | Claim unpublished events and publish with confirmation |
| Scheduler | Enqueue expiry, stale-listing, retention, and reconciliation jobs |
| Migration job | Apply reviewed schema changes once per deployment |

Phase 0 runtime entry points, Phase 1 REST routers, and the Phase 3 Slice 1 media handler on the
existing worker are confirmed in [the backend README](../backend/README.md). WebSocket, scheduler,
and other product-specific worker handlers are not implemented yet.

## Background jobs

- Media scanning, bounded decode/re-encode, metadata stripping, and private derivatives are
  implemented through `moderation_pending`; moderation submission is not implemented.
- Inferred preference recalculation and feed cache invalidation.
- Interest expiry and cooldown projection.
- Verification expiry and revocation propagation.
- FCM notification delivery and retry.
- Search/document projections if later introduced.
- Retention, anonymization, orphaned-upload cleanup.
- Outbox and DLQ reconciliation.

Schedulers enqueue work; workers own bounded processing. n8n does not own critical expiry or state transitions.

Media processing uses a media-local coordinator because external calls cannot run inside the
generic SQL consumer transaction. It claims and finalizes with short transactions; S3, scanner,
and Pillow work occurs between them. A final transaction commits media state, versioned evidence,
derivative rows, audit, and outbox together.

## Errors and logging

Return RFC 9457-style problem responses with stable code, title, status, correlation ID, and safe field errors. Do not expose stack traces or policy internals.

Structured logs include service role, environment, request/trace ID, actor ID when allowed, resource ID, operation, latency, and safe outcome code. Redaction occurs before emission.

## Dependency and migration policy

- Pin runtime dependencies and commit lock files after scaffold.
- Alembic migrations are forward-only in production; destructive changes use expand/migrate/contract.
- Generated SQL must be reviewed for locks, indexes, constraint validation, and rollback strategy.
- Provider SDKs stay behind adapters to avoid coupling verification or notification policy to a vendor.

See [database design](database-design.md), [API design](api-design.md), and [observability and testing](observability-testing.md).
