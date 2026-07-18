# WheelMatch architecture documentation

## Status

This directory is the implementation source of truth for the approved WheelMatch target architecture. Backend Phase 0 is implemented under `backend/`; later product phases and the Flutter application remain design-only. Consequently:

- Confirmed product decisions are marked **Accepted** and are normative.
- Implementation choices needed to make the design actionable but not explicitly confirmed are marked **Assumption** or **Proposed**.
- Backend commands are verified in `backend/README.md`; mobile and production deployment commands remain planned.
- The implementation must continue to follow the accepted decisions documented here.

## Target stack

| Area | Approved MVP choice |
|---|---|
| Mobile | Flutter, Riverpod, GoRouter, Dio; Android first and iOS-compatible |
| Backend | FastAPI modular monolith |
| Data | PostgreSQL with PostGIS |
| Cache and limits | Redis |
| Media | S3 with CloudFront |
| Jobs and events | SQS workers plus transactional outbox |
| Realtime | WebSockets with PostgreSQL persistence and Redis fan-out |
| Push | Firebase Cloud Messaging; APNs through FCM for iOS |
| Automation | Isolated n8n for non-critical asynchronous integrations |
| Hosting | AWS managed services and containerized workloads |
| Monitoring | Sentry, structured logs, metrics and traces |

## Document map

1. [Product requirements](product-requirements.md)
2. [System architecture](system-architecture.md)
3. [Mobile architecture](mobile-architecture.md)
4. [Backend architecture](backend-architecture.md)
5. [Database design](database-design.md)
6. [API design](api-design.md)
7. [Discovery and preferences](discovery-and-preferences.md)
8. [Interest, match and chat flow](interest-match-chat-flow.md)
9. [Authentication and authorization](authentication-authorization.md)
10. [Verification and moderation](verification-moderation.md)
11. [Media storage](media-storage.md)
12. [Notifications and n8n](notifications-and-n8n.md)
13. [Infrastructure and deployment](infrastructure-deployment.md)
14. [Security threat model](security-threat-model.md)
15. [Observability and testing](observability-testing.md)
16. [Implementation roadmap](implementation-roadmap.md)
17. [Architecture decisions](architecture-decisions.md)

## Non-negotiable domain boundaries

- A content reaction, bookmark, seller interest request, match, and conversation are different aggregates.
- Interested and Not Interested reactions never contact the seller.
- Only explicit Send Interest creates a seller-facing request.
- Only seller acceptance creates a match and conversation.
- PostgreSQL is authoritative; Redis, SQS, WebSockets, FCM, and n8n are projections or delivery mechanisms.
- Business-critical transitions stay inside FastAPI transactions.
- Personal listing publication requires current identity verification, vehicle-ownership verification, media readiness, and moderation approval.
- Personal exact coordinates and verification documents are never public.

## Assumptions and future decisions

| Topic | Current design position | Status |
|---|---|---|
| Pending interest expiry | Seven days; reminder around day three | Proposed, not confirmed |
| Authentication sessions | Short-lived access token plus rotating opaque refresh session | Proposed |
| Chat attachments | Text and system events only for MVP; image/document attachments deferred | Assumption |
| Saved-search notification cadence | Preference-controlled push; exact immediate/digest policy TBD | Future decision |
| Identity and ownership providers | Provider adapter interfaces; vendors not selected | Future decision |
| Infrastructure as code tool | Required, but Terraform/OpenTofu/CDK selection TBD | Future decision |
| Initial service objectives | Values in observability docs are launch targets, not contractual SLOs | Assumption |
| Multi-language and multi-currency | Not included in MVP unless launch market requires them | Future decision |

When an assumption is resolved, update [architecture decisions](architecture-decisions.md) and every directly affected contract before implementation.

## Implementation and external testing workflow

- The main Codex agent exclusively owns production code, migrations, dependencies, tests, and documentation.
- External AI agents and test runners operate on a separate clone/worktree at a fixed commit and must remain read-only.
- Before implementation, define a requirement-to-test impact matrix listing mandatory and explicitly excluded tests.
- During implementation, run only targeted Ruff, mypy, and affected tests.
- Test behavior at the lowest reliable layer; do not duplicate the same rule across unit, repository, API, and end-to-end tests.
- Run the complete backend acceptance suite once after targeted checks pass.
- Do not add or run tests for unrelated phases, mobile, unchanged infrastructure, or hypothetical behavior.
- External test reports must include exact commands, exit codes, concise failures, and no secret values.
- The main agent reviews failures and applies all fixes.