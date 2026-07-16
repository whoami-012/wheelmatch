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

## Verification privacy

Prefer provider-hosted document capture. Keep provider references and derived results. Sensitive vehicle/document data is encrypted, masked in review, excluded from normal logs and exports, and retained only as required.

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
