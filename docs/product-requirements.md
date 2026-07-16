# Product requirements

See [README](README.md) for document status and [architecture decisions](architecture-decisions.md) for rationale.

## Product objective

WheelMatch is a mobile marketplace where users discover cars and bikes through a swipe feed, publish verified listings, explicitly contact sellers, and unlock messaging only after seller acceptance.

## Actors and capabilities

- Every active user can browse, react, save, search, and act as a buyer.
- Seller is a capability activated when publication requirements are satisfied; it is not a registration account type.
- Dealer access is additive through active membership in a verified dealer organization.
- Dealer membership never removes personal buyer or seller capabilities.
- Platform moderators and administrators have purpose-limited operational access.

## Core user journeys

### Buyer

1. Register and verify email and phone.
2. Configure explicit vehicle and location preferences.
3. Browse a cursor-paginated swipe feed.
4. Mark listings Interested or Not Interested.
5. Save listings independently.
6. Explicitly Send Interest to a seller.
7. Chat only after the seller accepts.
8. Manage reactions, saved listings, requests, matches, blocks, reports, and notification preferences.

### Personal seller

1. Tap Sell without changing account type.
2. Create a private draft after email and phone verification.
3. Complete seller profile, listing details, location, and media.
4. Complete reusable identity verification.
5. Complete or reuse valid owner–vehicle verification.
6. Pass moderation for the current listing version.
7. Publish transactionally.
8. Manage availability, requests, accepted conversations, and listing lifecycle.

### Dealer member

1. Operate in a per-action context: Sell as Personal or Sell as Organization.
2. Manage organization listings only with current membership permission.
3. Accept interest only with active sales permission.
4. Become the initial assigned agent on acceptance.
5. Read and reply only while the active assignee.

## Domain separation

| Domain | States or behavior | Side effects |
|---|---|---|
| Content reaction | Interested, Not Interested, or neutral/no row | Recommendation only |
| Saved listing | Private bookmark | Weak recommendation signal |
| Interest request | Pending, accepted, rejected, withdrawn, expired | Seller notification |
| Match | Created after acceptance | Grants conversation relationship |
| Conversation | Assigned and active/read-only/closed | Enables authorized messaging |

Changing a reaction or resetting inferred preferences must not change bookmarks, requests, matches, or chats.

## Listing requirements

A listing supports:

- Car or bike type with normalized make, model, variant, year, registration year, price, negotiable flag, odometer, fuel, transmission, ownership count, registration jurisdiction, colour, insurance validity, service history, condition, and description.
- Type-specific attributes in controlled relational columns, not uncontrolled JSON.
- Multiple sanitized images in deterministic order.
- Private exact location for server-side distance filtering.
- Lifecycle states including draft, available, reserved, sold, withdrawn, and expired.
- Separate publication, moderation, and verification gates.

Exactly one listing owner is required: an individual user or a dealer organization.

## Publication requirements

Personal publication requires:

1. Active user, verified email and phone.
2. Active seller profile.
3. Complete current listing version.
4. Sanitized and approved media.
5. Current reusable identity verification.
6. Current owner–canonical-vehicle verification, with 180-day default freshness.
7. Moderation approval for the same listing version.
8. No conflicting live personal listing for the canonical vehicle.

Pending or failed verification keeps the listing private and non-contactable. Dealer inventory follows a separate organization policy.

## Location privacy

- Personal listing responses expose locality, coarse area, and a distance band only.
- Personal APIs never expose exact coordinates, street addresses, internal geospatial cells, or exact pins.
- Verified dealers may publish an organization-owned showroom address only when explicitly enabled.
- Exact meetup locations are shared intentionally after a match and never overwrite listing location.

## Functional MVP scope

- Email/phone registration and session management.
- Profiles, seller onboarding, dealer organizations and memberships.
- Private listing drafts, verified publication, media processing, lifecycle management.
- Swipe discovery, reactions, explicit preferences, saved listings, search, and location filters.
- Interest-request lifecycle with status-specific retry rules.
- Match and text chat with read receipts and dealer assignment.
- FCM push, in-app notifications, reports, blocks, moderation queues, and audit logs.
- Admin functions needed for verification, moderation, suspension, and assignment oversight.

## Explicit MVP exclusions

- Payments, deposits, escrow, finance, or title transfer.
- Public unverified personal listings.
- Shared dealer inbox with unrestricted message access.
- Machine-learned ranking.
- Kubernetes, Kafka, OpenSearch, or microservices.
- Request reactivation or withdrawal undo.
- Public personal exact location.
- Direct n8n access to production application tables.

## Quality attributes

| Attribute | Requirement |
|---|---|
| Correctness | Transactional invariants and idempotent commands |
| Privacy | Minimize location, identity, behavioral, and conversation exposure |
| Security | Object-level authorization, scoped credentials, encrypted sensitive data |
| Availability | Core reads and transactions degrade safely when integrations fail |
| Performance | Cursor pagination, indexed PostGIS queries, bounded workers, caching |
| Auditability | Append-only state histories for sensitive transitions |
| Operability | Structured logs, trace correlation, health checks, DLQs, restore drills |
| Portability | Flutter codebase supports Android first and iOS without architecture changes |

Detailed non-functional validation is in [observability and testing](observability-testing.md).
