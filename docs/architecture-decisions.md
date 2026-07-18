# Architecture decisions

Statuses:

- **Accepted:** confirmed product or architecture decision.
- **Proposed:** implementation-ready recommendation awaiting explicit approval.
- **Future:** intentionally unresolved.

## ADR-001 — modular monolith

**Status:** Accepted.

Use one FastAPI codebase with domain modules and independently scalable API, WebSocket, worker, scheduler, and outbox roles. Avoid microservices until measured scaling or ownership needs justify extraction.

**Consequences:** simpler transactions and operations; requires strict module boundaries.

## ADR-002 — Flutter mobile stack

**Status:** Accepted.

Use Flutter, Riverpod, GoRouter, and Dio. Launch Android first while isolating platform APIs so iOS uses the same feature code.

**Consequences:** shared product logic; iOS build/signing still requires macOS validation.

## ADR-003 — PostgreSQL/PostGIS source of truth

**Status:** Accepted.

PostgreSQL owns all authoritative state and PostGIS spatial filtering. Redis is cache, limit, feed-session, presence, and fan-out infrastructure only.

**Consequences:** strong consistency and simpler recovery; database indexes/query discipline are critical.

## ADR-004 — distinct reaction, bookmark, request, match, and chat domains

**Status:** Accepted.

Interested/Not Interested are private recommendation signals. Saved listing is a bookmark. Explicit Send Interest creates a seller request. Seller acceptance creates match and conversation.

**Consequences:** avoids accidental seller spam and lifecycle coupling; UI needs distinct actions.

## ADR-005 — reaction current state plus append-only history

**Status:** Accepted.

Store one current reaction per user/listing and append every change/removal to reaction events. Neutral means no current row.

**Consequences:** reliable audit/debugging with additional retention/storage work.

## ADR-006 — deterministic recommendation MVP

**Status:** Accepted.

Use explicit hard filters, deterministic ranker-v1, bounded behavioral features, and 10–15% exploration. Recalculate inferred profiles asynchronously through SQS.

**Consequences:** explainable and fast to tune; less personalization than mature ML.

## ADR-007 — pair-level interest thread and two attempts

**Status:** Accepted.

interest_request_thread is the lock/lifecycle boundary. Attempt 1 withdrawal permits final attempt after 24 hours; normal expiry permits final attempt after 14 days. Rejection and attempt-2 terminal states are final.

**Consequences:** strong anti-spam policy and safe concurrency; users have limited recovery.

## ADR-008 — no request reactivation

**Status:** Accepted.

Withdrawal is irreversible for MVP. Attempt 1 consumes the only retry; attempt 2 cannot be retried.

**Consequences:** simpler state model; mobile confirmation must be explicit.

## ADR-009 — unified user capabilities

**Status:** Accepted.

No buyer/seller account type. Every active user can buy; seller is derived readiness; dealer access is additive membership.

**Consequences:** avoids account switching; authorization evaluates more context.

## ADR-010 — exactly one listing owner

**Status:** Accepted.

A listing belongs to a personal user or dealer organization, enforced by columns and check constraint. Creator is attribution, not owner.

**Consequences:** clear lifecycle and employee departure semantics.

## ADR-011 — dealer role and membership authorization

**Status:** Accepted.

Use active membership and centralized permissions. Organization suspension does not suspend personal accounts; account suspension removes all member capabilities.

**Consequences:** immediate cache/socket invalidation is required.

## ADR-012 — one active dealer conversation assignee

**Status:** Accepted.

Only the active assigned sales agent reads/replies. Owners/admins have metadata-only inbox and must explicitly take over. Reassignment is audited and buyer-visible.

**Consequences:** strong privacy/accountability; unassigned queue requires operations.

## ADR-013 — private personal listing coordinates

**Status:** Accepted.

Store exact point privately. Personal APIs expose locality, coarse area, and distance bands only. Verified dealers may explicitly publish an organization showroom address.

**Consequences:** safer sellers; map UX and search must resist inference.

## ADR-014 — meetup location is separate

**Status:** Accepted.

Exact meetup sharing is explicit, post-match, conversation-scoped, revocable/expiring, and never overwrites listing location.

**Consequences:** clean consent boundary; recipients may retain already displayed data.

## ADR-015 — verification before personal publication

**Status:** Accepted.

Private drafts are allowed after email/phone verification. Public personal listings require valid identity, owner–vehicle verification, media, and moderation. No unverified public tier.

**Consequences:** reduced fraud; higher publication friction and manual review burden.

## ADR-016 — owner–canonical-vehicle verification reuse

**Status:** Accepted.

Ownership verification belongs to user–canonical-vehicle relationship and may be reused within a configurable 180-day default when fingerprint and risk state remain valid. Every publication records a new ownership check.

**Consequences:** less repeated document upload; canonical identity and revocation propagation become critical.

## ADR-017 — one live personal listing per canonical vehicle

**Status:** Accepted.

Use transactional canonical-vehicle locking plus a partial unique index. Relisting creates a new linked listing and closes the old listing atomically on successful publication.

**Consequences:** preserves history and prevents duplicates; canonical conflicts need review.

## ADR-018 — presigned media with sanitization

**Status:** Accepted.

Upload directly to private S3 quarantine using constrained intents. Scan, decode, re-encode, strip metadata, moderate, and serve only sanitized derivatives through CloudFront.

**Consequences:** scalable and secure; asynchronous processing state is required.

## ADR-019 — WebSocket delivery after persistence

**Status:** Accepted.

PostgreSQL stores messages before acknowledgement/fan-out. WebSockets deliver live events; REST cursor history recovers gaps.

**Consequences:** no committed-message loss; each send incurs a database transaction.

## ADR-020 — transactional outbox and SQS

**Status:** Accepted.

Commit domain mutation and event together, then relay to SQS. Consumers are idempotent with bounded retry and DLQ.

**Consequences:** reliable side effects; outbox lag and replay need operations.

## ADR-021 — n8n is isolated automation

**Status:** Accepted.

n8n handles non-critical integrations, reminders, alerts, reports, and moderation orchestration through scoped APIs. It does not implement critical state or access production tables directly.

**Consequences:** protects consistency; requires an automation gateway and callback contracts.

## ADR-022 — PostgreSQL search first

**Status:** Accepted.

Use full-text, trigram, typed filters, and PostGIS for MVP. Add OpenSearch only after measured need.

**Consequences:** less infrastructure; complex large-scale facets may later need a projection.

## ADR-023 — opaque cursor pagination

**Status:** Accepted.

Use signed filter-bound keyset cursors for feed, reactions, saved listings, searches, messages, and queues.

**Consequences:** stable scalable pagination; arbitrary page jumps are unsupported.

## ADR-024 — AWS managed MVP

**Status:** Accepted.

Use containerized FastAPI roles with managed PostgreSQL, Redis, SQS, S3/CloudFront, and Secrets Manager. Avoid Kubernetes/Kafka for MVP.

**Consequences:** lower operational load; provider-specific infrastructure adapters remain.

## ADR-025 — FCM push

**Status:** Accepted.

Use FCM for Android and APNs through FCM for iOS. Push is a hint; in-app state is authoritative.

**Consequences:** unified client integration; delivery is not guaranteed and must not carry sensitive content.

## Additional accepted, proposed, and future ADRs

### ADR-P01 — session mechanism

**Status:** Accepted on 2026-07-17.

Use short-lived signed access tokens plus rotating opaque refresh sessions with family reuse detection. Social login and MFA remain future decisions and are not part of the accepted Phase 1 session mechanism.

### ADR-P02 — pending request expiry

**Status:** Proposed.

Expire pending requests after seven days with an optional reminder around day three. Cooldown rules remain accepted regardless of this configurable duration.

### ADR-P03 — MVP chat content

**Status:** Proposed.

Text and system events only. Attachments require a separate media, moderation, retention, and abuse decision.

### ADR-P04 — retention values

**Status:** Proposed.

Use baselines in [security threat model](security-threat-model.md), subject to launch jurisdiction and compliance review.

### ADR-F01 — providers and IaC

**Status:** Future.

Select identity/ownership verification vendors, moderation providers, IaC tool, launch region, and exact RPO/RTO through separate decisions.

## Decision maintenance

A decision change must update:

1. This ADR.
2. Affected database constraints and state machines.
3. API contracts and failure codes.
4. Mobile UX states.
5. Migration/backfill and compatibility plan.
6. Security and test coverage.
