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
| POST | /auth/verify-email | Verify email challenge |
| POST | /auth/verify-phone | Verify phone challenge |
| POST | /auth/recovery/* | Password/account recovery |
| GET/PATCH | /me/profile | Personal profile |
| GET | /me/capabilities | Effective buyer/seller/dealer capabilities |
| GET/PATCH | /me/notification-preferences | Notification settings |

### Seller, dealer, and listing

| Method | Path | Purpose |
|---|---|---|
| GET | /me/seller-readiness | Missing seller requirements |
| POST/PATCH | /me/seller-profile | Create/update seller profile |
| GET | /me/dealer-memberships | Active and historical memberships |
| POST | /listings | Create private draft with owner context |
| GET/PATCH | /listings/{id} | Authorized listing read/update |
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

### Media

| Method | Path | Purpose |
|---|---|---|
| POST | /media/upload-intents | Create constrained presigned upload |
| POST | /media/{media_id}/complete | Confirm object and enqueue processing |
| GET | /media/{media_id}/status | Scan/sanitize/moderation state |
| DELETE | /media/{media_id} | Remove draft media or schedule deletion |

See [media storage](media-storage.md).

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

Callbacks carry event/provider IDs and are idempotent. They never accept a caller-supplied user/listing identity without resolving the stored provider reference.

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
