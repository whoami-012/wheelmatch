# Security and threat model

## Protected assets

- Accounts, sessions, dealer memberships, and permissions.
- Listing ownership and publication state.
- Interest, match, assignment, message, and block state.
- Exact personal coordinates and meetup shares.
- Identity/ownership documents and vehicle identifiers.
- Media originals and sanitized derivatives.
- Behavioral preferences and inferred profiles.
- Secrets, provider credentials, audit records, and administrative actions.

## Trust boundaries

- Mobile device ↔ edge/API/WebSocket.
- API ↔ PostgreSQL/Redis/SQS/S3.
- Worker ↔ external providers/FCM.
- Backend ↔ isolated n8n.
- Moderator/admin ↔ sensitive review tooling.
- Dealer organization ↔ individual members and assignments.

## Required controls

### Identity and session

- Argon2id password hashing when passwords are used.
- Verified email and phone before draft creation.
- Proposed refresh-token rotation with family reuse detection.
- Brute-force, credential-stuffing, OTP, recovery, and device-rate limits.
- MFA/step-up authentication for high-impact administration and organization ownership.
- Secure mobile storage and revocable device sessions.

### Authorization and IDOR prevention

- Deny by default.
- Reauthorize every object operation from current database state.
- Do not trust user, owner, organization, assignment, or listing IDs from client claims.
- Separate metadata permission from message-body permission.
- Purpose-limit moderator access and audit it.
- Avoid long-lived authorization claims for membership/assignment.

### API protections

- Strict Pydantic schemas, bounds, allowlists, and request-body limits.
- Parameterized SQLAlchemy queries.
- Stable generic errors where detailed state enables enumeration.
- WAF plus application rate limits keyed by user/device/IP/resource.
- Idempotency and optimistic concurrency for retried commands.
- CORS restricted to approved web/admin origins if introduced; mobile is not protected by CORS.
- CSRF protection for any cookie-authenticated browser/admin surface.
- Output encoding and CSP for future web/admin UI.

### Media and SSRF

- Presigned uploads constrained by owner/key/type/size/expiry.
- MIME, magic bytes, extension, checksum, decode, malware, and decompression-bomb checks.
- Re-encode images and strip all metadata, especially GPS.
- Private S3 and no original download.
- URL-fetching providers use allowlists, DNS/IP revalidation, redirect limits, and private-network blocking.

### Secrets and encryption

- TLS in transit; managed encryption at rest.
- Secrets Manager/KMS; no secrets in source, logs, n8n parameters, or mobile binaries.
- Separate KMS keys and access roles for documents, media, database, and n8n.
- HMAC vehicle identifiers with managed pepper and version.
- Encrypt retrievable sensitive identifiers and meetup address text.

## Threats and mitigations

| Threat | Mitigation |
|---|---|
| Account takeover | Rotation, reuse detection, MFA for high risk, anomaly/rate controls |
| Listing ownership IDOR | Exactly-one owner constraint plus object policy |
| Dealer ex-member access | Current membership/assignment checks and socket revocation |
| Silent admin chat access | Metadata-only admin view; explicit audited takeover |
| Duplicate request/match/chat | Pair thread locks, unique constraints, idempotency |
| Swipe/recommendation poisoning | Limits, diminishing returns, anomaly detection |
| Seller learns reactions | No seller API/analytics exposure |
| Spatial trilateration | Distance bands, minimum/discrete radius, mixed ordering, probe detection |
| EXIF location leak | Server decode/re-encode and metadata verification |
| Document leak | Isolated storage, purpose access, no logs/analytics/n8n |
| Malicious upload | Quarantine, signature/decode/scan limits |
| Message spam/harassment | Match gate, limits, block/report, spam scoring |
| WebSocket authorization race | Reauthorize send/read; authorization_version and revocation |
| Event replay | Event/idempotency tables and signed timestamped envelopes |
| n8n compromise | Scoped credentials, API-only access, egress limits, no DB access |
| Provider callback forgery | Signature, timestamp, replay, reference resolution |
| Stale cache exposes removed listing | Source-state serialization plus invalidation |
| Privileged misuse | Least privilege, case/purpose fields, immutable audit, alerts |

## Location privacy

- exact_point is never included in personal listing DTOs, logs, analytics, n8n, push, or client telemetry.
- PostGIS filtering is server-side.
- Do not expose exact result counts or stable exact-distance order for narrow searches.
- Dealer exact pin requires active verified organization and public address setting.
- Unavailable listings expose no location.
- Meetup share is a separate explicit post-match resource with expiry/revocation.

Backend Phase 2 stores personal points only in the PostGIS persistence entity and maps authorized
responses through a coordinate-free projection. Spatial predicates return listing identifiers,
not exact distances. Location audit/outbox records contain visibility/version only. Dealer public
business pins resolve the stored point from a currently verified, published organization address;
caller-supplied coordinates cannot define that pin.

## Phase 2 media boundary

Quarantine objects remain private under backend-generated prefixes. Upload intents are bound to a
currently authorized draft and sign MIME, size, checksum, key, and expiry constraints. Completion
does S3 HEAD outside the database transaction, reauthorizes and re-locks current state, and emits
only media/listing identifiers plus processing version. Object keys and upload URLs are excluded
from audit/outbox payloads and response status contracts.

## Phase 3 Slice 1 media sanitization boundary

The worker independently validates stored bytes, size, SHA-256, signature, MIME, decoder format,
dimensions, and decoded pixel count. It applies orientation and creates new JPEG pixel buffers so
EXIF/GPS, XMP, IPTC, comments, ICC profiles, thumbnails, and unknown input chunks are not copied.
Quarantine and derivative objects remain private under backend-generated constrained prefixes.

Processing evidence and derivative keys/hashes are persistence-only. Owner APIs and outbox events
expose only allowlisted identifiers, processing version, state, and safe failure code. Duplicate
and stale events are version-gated. `moderation_pending` grants no public access or publication
eligibility. No production malware provider is selected, so disabled/test scanners fail closed
outside local/test environments.

## Verification privacy

Phase 3 Slice 2 uses provider-hosted capture. Capture URLs are response-only and excluded from
persistence, logs, audit, outbox, and status contracts. Provider references and result IDs remain
private persistence fields; self-service APIs expose only effective state, allowlisted assurance,
lifecycle timestamps, projection version, and safe failure code. Result replay is unique and
idempotent, terminal conflicts fail safely, and a superseded attempt cannot overwrite the current
user projection. Disabled/deterministic providers are rejected outside local/test environments.

## Phase 3 Slice 3 vehicle identity boundary

Registration, VIN, and chassis values are normalized transiently and keyed with domain-separated,
versioned HMAC-SHA256 before persistence. Plain hashes, raw identifiers, capture URLs, provider
payloads, document evidence, and internal decisions are excluded from database projections,
audit, outbox, logs, and APIs. Only private provider/object references may be retained with
explicit retention metadata. A current active personal owner and current verified identity are
revalidated before serialization and result effectiveness; `created_by_user_id` grants no owner
access. Stale identity/canonical material cannot become an effective verified result. Local/test
normalizers and providers are rejected in staging/production.

No identity documents, legal names, birth dates, document numbers, provider payloads, scores, or
raw assurance evidence are stored. Identity evidence cannot enter listing-media storage. No public
provider webhook exists until provider selection, signature verification, replay window, and
service-authentication contracts are accepted.

## Phase 3 Slice 4 submission-readiness boundary

Object authorization precedes sensitive evidence reads. Personal readiness DTOs expose only gate
name, safe state/code, and remediation action; exact/private location, addresses, provider and
document references, HMACs, material/media fingerprints, object keys, checksums, URLs, and fraud
internals remain persistence-only. Submission locks current source evidence and commits attempt,
audit, idempotency, and allowlisted outbox state atomically without provider/network/S3 calls.
Stored attempt evidence is version/fingerprint gated and never grants publication eligibility.

## Phase 3 Slice 5 ownership-reuse boundary

Reuse is fail-closed across owner, canonical vehicle, current identity attempt/projection,
canonical identity/hash versions, ownership basis, fingerprint shape/version binding, lifecycle,
provider expiry, policy freshness, and newer-conflict state. Restricted vehicle identity states
cannot be reused. Command selection is serialized by the target listing lock and atomically
records idempotency, allowlisted audit, and outbox state without provider calls. GET evaluation is
read-only. Responses and events omit the source listing, raw/keyed identifiers, fingerprints,
provider/document references, evidence payloads, fraud signals, exact location, and media data.

## Abuse prevention

- User/device/IP limits for registration, OTP, reactions, requests, messages, reports, uploads, searches, and resets.
- Pair-level maximum two interest attempts.
- Self-contact and controlled-organization contact prevention.
- Duplicate media/listing/vehicle detection.
- High report volume, repeated listing creation, abnormal requests, and rapid spatial probing feed risk review.
- Blocking is authoritative and not disclosed through detailed public errors.
- Reports cannot directly suspend a user; policy/risk/moderator decision is required.

## Privacy retention baseline

| Class | Baseline |
|---|---|
| Raw behavioral events | 90 days |
| Reaction history | 12 months |
| Inferred aggregates | 12 months or reset |
| Messages | Proposed 24 months after closure; legal/product confirmation required |
| Verification documents | Minimum compliance/legal period; provider-specific |
| Audit/security evidence | Policy/legal-hold driven |
| Abandoned uploads | Short lifecycle, typically days |

Support account export, deletion/anonymization, legal hold, and crypto-shredding where appropriate.

## Security validation

Before launch:

- Threat-model review for every trust boundary.
- OWASP ASVS Level 2-oriented API review.
- Mobile storage, certificate/TLS, deep-link, and reverse-engineering assessment.
- Object-level authorization matrix tests.
- Upload and image-parser fuzz/adversarial tests.
- WebSocket reassignment/revocation race tests.
- Location-inference and API-enumeration assessment.
- External penetration test and remediation verification.
