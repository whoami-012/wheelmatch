# API design

## Conventions

- REST base path: /api/v1.
- JSON request/response with UTC ISO-8601 timestamps and UUID strings.
- WebSockets only for realtime events; REST remains the recovery and history path.
- Mutating commands that may be retried require Idempotency-Key.
- Optimistic concurrency uses expected_version or If-Match where lost updates matter.
- Collections use opaque cursor pagination; clients never decode cursors.
- Errors use application/problem+json with stable domain codes.
- Internal automation APIs use a separate audience, network policy, and scoped service identity.

## Cursor contract

A cursor is signed and binds:

- Last sort values and unique ID.
- Filter hash.
- Feed or query snapshot/version.
- Normalized location context when relevant.
- Expiry.

Changing filters or search origin requires a new cursor. Offset pagination is not exposed for feed, reactions, listings, messages, or audit queues.

## Public API boundaries

### Authentication and profile

| Method | Path | Purpose |
|---|---|---|
| POST | /auth/register | Create account without account-type selection |
| POST | /auth/login | Start authenticated session |
| POST | /auth/refresh | Rotate refresh session |
| POST | /auth/logout | Revoke current session |
| POST | /auth/logout-all | Revoke every session for current user |
| POST | /auth/password/change | Change password and revoke every session |
| POST | /auth/verify-email | Verify email challenge |
| POST | /auth/verify-phone | Verify phone challenge |
| POST | /auth/recovery/request | Accept a generic password-recovery request |
| POST | /auth/recovery/reset | Consume recovery token and revoke every session |
| GET/PATCH | /me/profile | Personal profile |
| GET | /me/sessions | List current user's session families |
| DELETE | /me/sessions/{id} | Revoke only a current user's session family |
| POST | /me/identity-verifications | Idempotently start/resume provider-hosted identity capture |
| GET | /me/identity-verification | Privacy-safe effective identity-verification state |
| GET | /me/capabilities | Effective buyer/seller/dealer capabilities |
| GET/PATCH | /me/notification-preferences | Notification settings |

### Seller, dealer, and listing

| Method | Path | Purpose |
|---|---|---|
| GET | /me/seller-readiness | Missing seller requirements |
| POST | /me/seller-profile | Create optional seller profile |
| GET | /me/dealer-memberships | Active and historical memberships |
| POST | /dealer-organizations | Create organization with owner membership |
| GET | /dealer-organizations/{id} | Read organization through current membership |
| POST | /dealer-organizations/{id}/memberships | Invite member with explicit role |
| PATCH | /dealer-organizations/{id}/memberships/{membership_id} | Role/status transition with expected version |
| POST | /me/dealer-memberships/{membership_id}/accept | Consume single-use invitation |
| POST | /me/dealer-memberships/{membership_id}/leave | Leave with expected version |
| POST | /listings | Create private draft with owner context |
| GET/PATCH | /listings/{id} | Authorized listing read/update |
| POST | /listings/{id}/ownership-verification/start | Start/resume personal owner–vehicle capture |
| GET | /listings/{id}/ownership-verification/status | Privacy-safe personal ownership state |
| POST | /listings/{id}/submit | Start verification/moderation readiness flow |
| POST | /listings/{id}/publish | Idempotent publication command |
| POST | /listings/{id}/relist | Create linked private relist draft |
| PUT | /listings/{id}/availability | Available/reserved/sold/withdrawn |
| GET | /me/listings | Owner-context filtered listings |
| GET | /listings/{id}/publication-readiness | Backend-computed gate states |

Owner context is explicit in creation:

```json
{
  "owner_context": {
    "type": "dealer_organization",
    "organization_id": "uuid"
  },
  "vehicle_type": "car"
}
```

The backend never trusts organization_id without active membership and permission checks.

Phase 3 Slice 3 implements only the two ownership-verification routes above. Start requires
`Idempotency-Key`, an optimistic listing version, current personal ownership, and current verified
identity. The provider-hosted capture URL appears only in the start response. Status omits raw and
keyed identifiers, provider references/result IDs, documents, payloads, evidence, scores, capture
URLs, and internal reasons. Provider results enter through an application-service boundary; no
public ownership-provider webhook is defined.

Phase 3 Slice 4 implements `POST /listings/{id}/submit` and
`GET /listings/{id}/publication-readiness` for personal listings only. Submit requires
`Idempotency-Key` plus `expected_version`; identical retries replay the stored response and
conflicting key reuse returns `IDEMPOTENCY_KEY_CONFLICT`. Readiness is read-only and recomputes
current gates. Responses expose only listing/submission state plus gate name, safe state/code, and
remediation action. Exact location, provider/document evidence, HMACs, fingerprints, media keys,
hashes, and URLs are never serialized. Authorized dealer operators receive
`DEALER_SUBMISSION_NOT_IMPLEMENTED`; other callers retain safe not-found behavior. Moderation and
publication remain unimplemented, so `publishable` is always false.

Phase 3 Slice 5 extends ownership start/status and submission/readiness with the safe `reused`
projection. Eligible cross-listing personal evidence returns the original ownership attempt
without creating a provider session or another attempt. Submission/readiness expose only the
reuse boolean and retain the selected verification internally; source listing IDs, raw/keyed
vehicle identifiers, fingerprints, provider/document references, evidence, and internal risk
reasons remain excluded. GET status/readiness do not write audit or outbox state. Dealer ownership
reuse remains unsupported, and moderation/publication behavior is unchanged.

The Phase 1 endpoints above are implemented. Backend Phase 2 implements private draft create/read/
update and current-owner listing queries. Draft creation requires `Idempotency-Key`; updates use
`expected_version`; current-owner queries use signed, expiring, filter-bound keyset cursors.
Publication, relisting, and availability routes remain Phase 3 roadmap contracts.

### Catalogue and private location

| Method | Path | Purpose |
|---|---|---|
| GET | /catalogue/makes | Bounded make browse by car/bike classification |
| GET | /catalogue/models | Bounded make-child model browse |
| GET | /catalogue/variants | Bounded model-child variant browse |
| GET | /catalogue/search | Bounded normalized controlled-taxonomy search |
| GET/PUT | /listings/{id}/location | Authorized privacy-safe projection/private point write |

Personal location responses expose locality, coarse area, and an optional distance band only.
They have no latitude, longitude, address, internal cell, or exact-distance field. A dealer public
business address can be selected only from a verified, published address belonging to the current
listing organization; caller coordinates are not used for its public pin.

### Media

| Method | Path | Purpose |
|---|---|---|
| POST | /media/upload-intents | Create constrained presigned upload |
| POST | /media/{media_id}/complete | Confirm object and enqueue processing |
| GET | /media/{media_id}/status | Scan/sanitize/moderation state |
| DELETE | /media/{media_id} | Remove draft media or schedule deletion |

See [media storage](media-storage.md).

Backend Phase 2 implements these four media routes for private drafts. Upload intent creation is
idempotent and returns a short-lived constrained PUT. Completion validates the stored private
object outside the database transaction, then commits processing state, audit, and the durable
`media.processing.requested` event together. Ready/moderation/publication states are not exposed.

### Discovery and preferences

| Method | Path | Purpose |
|---|---|---|
| GET | /discovery/feed | Ranked cursor feed |
| POST | /discovery/feed/reset | New feed session, not preference deletion |
| GET | /listings/search | Structured full-text and PostGIS search |
| GET/POST | /me/search-subscriptions | List or create a saved filter |
| PATCH/DELETE | /me/search-subscriptions/{id} | Change cadence/preferences or remove saved filter |
| PUT | /listings/{id}/reaction | Interested or Not Interested |
| DELETE | /listings/{id}/reaction | Return to neutral |
| GET | /me/reactions | Manage reaction history |
| PUT/DELETE | /me/saved-listings/{listing_id} | Bookmark lifecycle |
| GET | /me/saved-listings | Cursor-paginated bookmarks |
| GET/PATCH | /me/discovery-preferences | Explicit filters |
| POST | /me/inferred-preferences/reset | Advance inference generation |
| GET/DELETE | /me/hidden-sellers/{seller_id} | Hide/unhide seller |

Reaction request:

```json
{
  "reaction": "not_interested",
  "client_action_id": "uuid",
  "expected_version": 3
}
```

### Interest, match, and conversation

| Method | Path | Purpose |
|---|---|---|
| GET | /listings/{id}/contact-eligibility | Advisory current lifecycle state |
| POST | /listings/{id}/interest-requests | Create attempt 1 or eligible attempt 2 |
| GET | /me/interest-requests | Buyer request history |
| GET | /seller/interest-requests | Authorized personal/dealer request queue |
| POST | /me/interest-requests/{id}/withdraw | Irreversible withdrawal |
| POST | /seller/interest-requests/{id}/accept | Atomically accept, match, converse, assign |
| POST | /seller/interest-requests/{id}/reject | Permanently close pair |
| GET | /matches | Current matches |
| GET | /conversations | Authorized conversation metadata |
| GET | /conversations/{id}/messages | Cursor history |
| POST | /conversations/{id}/messages | Idempotent message send |
| POST | /conversations/{id}/read | Advance read cursor |
| POST | /dealer/conversations/{id}/assignment | Assign, reassign, or take over |
| POST | /conversations/{id}/location-shares | Explicit post-match meetup share |

Interest and chat behavior is specified in [interest, match and chat flow](interest-match-chat-flow.md).

### Safety and administration

| Method | Path | Purpose |
|---|---|---|
| PUT/DELETE | /users/{id}/block | Safety block lifecycle |
| POST | /reports | Report user, listing, or message |
| GET | /notifications | In-app notification history |
| POST | /notifications/{id}/read | Mark read |
| GET | /admin/moderation-cases | Purpose-scoped queue |
| POST | /admin/moderation-cases/{id}/decision | Structured decision |
| POST | /admin/verifications/{id}/decision | Manual review |
| POST | /admin/users/{id}/suspend | Audited account action |
| POST | /admin/organizations/{id}/suspend | Organization-only action |

## Internal APIs

Use /internal/v1 with service authentication, mTLS where practical, strict audience, and scopes such as moderation:write or notification:deliver.

- Verification provider callbacks.
- Media worker results.
- n8n event intake/callbacks.
- Notification delivery results.
- Recommendation/profile worker results.
- Moderation and analytics batch claims.
- DLQ and automation failure reporting.

Callbacks carry event/provider IDs and are idempotent. They never accept a caller-supplied
user/listing identity without resolving the stored provider reference. Phase 3 Slice 2 exposes no
public identity-provider callback; provider-result ingestion is an application-service boundary
until the production provider, signature, and replay contract are accepted.

## WebSocket contract

Connection endpoint: /api/v1/ws using a short-lived, single-use ticket minted over REST.

Client events:

- conversation.subscribe
- conversation.unsubscribe
- message.send
- message.read
- typing.start / typing.stop

Server events:

- message.ack
- message.created
- message.read
- conversation.system_event
- conversation.authorization_revoked
- conversation.state_changed
- error

Every event includes event_id, conversation_id where applicable, server timestamp, and sequence/cursor. Message acknowledgement follows PostgreSQL commit.

## Problem response

```json
{
  "type": "urn:wheelmatch:error:request-state-changed",
  "title": "Request state changed",
  "status": 409,
  "code": "REQUEST_STATE_CHANGED",
  "correlation_id": "uuid",
  "field_errors": []
}
```

The type is a stable identifier. Production documentation should publish a safe error catalog.

## Status usage

- 400: malformed syntax.
- 401: missing/invalid session.
- 403: known resource but action denied where disclosure is safe.
- 404: absent or existence-hidden resource.
- 409: lifecycle, idempotency, version, or uniqueness conflict.
- 422: structurally valid request with field/readiness errors.
- 429: actual rate limiting only.
- 503: transient dependency unavailable with safe retry guidance.
