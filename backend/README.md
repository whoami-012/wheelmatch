# WheelMatch backend

Backend Phase 3 Slice 5 adds reusable personal owner-vehicle verification across ownership
start/status and submission/readiness. It does not implement publication-time ownership checks,
moderation decisions, or publication.

## Phase 3 Slice 5 reusable personal ownership

- Reuse requires the same active personal owner and canonical vehicle, unchanged current identity
  and vehicle identity/hash versions, compatible ownership basis, valid bound fingerprint, active
  vehicle identity state, unexpired provider proof, and no newer conflicting attempt.
- Effective expiry is `min(original expires_at, verified_at + configured freshness)`, defaulting
  to 180 days. Reuse never changes the original verification timestamps.
- Eligible ownership start returns the original attempt with `reused=true`, without a provider
  call or new attempt. Listing-lock serialization and idempotency suppress duplicate audit/outbox
  results under retries and concurrent equivalent starts.
- Submission attempts retain the selected ownership verification, reuse flag, positive policy
  version, and immutable effective expiry. Readiness passes the ownership gate but moderation
  remains pending and `publishable` remains false.
- `WHEELMATCH_OWNERSHIP_REUSE_FRESHNESS_DAYS` and
  `WHEELMATCH_OWNERSHIP_REUSE_POLICY_VERSION` configure bounded policy inputs. Responses/events
  omit source listings, identifiers/HMACs, fingerprints, and provider/document evidence.

## Phase 3 Slice 4 personal submission readiness

- `POST /api/v1/listings/{listing_id}/submit` requires `Idempotency-Key` and `expected_version`,
  records or resumes one personal submission attempt, and recomputes every gate from PostgreSQL.
- `GET /api/v1/listings/{listing_id}/publication-readiness` is read-only and exposes only gate
  names, safe states/codes, and remediation actions. Exact location, provider evidence, keyed
  identifiers, fingerprints, object keys, hashes, and URLs are excluded.
- Current personal ownership, seller readiness, complete typed details, canonical vehicle,
  private location, current identity/ownership verification, and fully sanitized active media are
  required before `listing.moderation.requested` is written to the transactional outbox.
- Moderation is not implemented. Even with all pre-moderation gates passing, status is
  `moderation_pending`, `publishable` remains false, and no public/CDN/discovery state is created.
- Authorized dealer operators receive `DEALER_SUBMISSION_NOT_IMPLEMENTED`; unrelated callers
  retain safe not-found behavior. Listing/media changes make stored submission evidence stale
  without making the draft non-editable.

Phase 0 provides the production-shaped FastAPI foundation. Backend Phase 1 adds identity,
profile, seller-readiness, dealer membership, centralized authorization, audit, and session
modules. Backend Phase 2 adds the controlled vehicle catalogue, private personal/dealer listing
drafts, typed car/bike specifications, private PostGIS locations, and private quarantine media
upload intents. Backend Phase 3 Slice 1 adds asynchronous private image validation, scanning,
sanitization, and derivative evidence. Slice 2 adds provider-neutral user identity-verification
attempts and an effective user projection. Slice 3 adds keyed canonical vehicle identity and
provider-neutral personal owner–vehicle verification. Real verification providers, public
callbacks, document bytes, moderation decisions, publication, discovery, and mobile remain
unimplemented.

## Phase 3 Slice 3 personal owner–vehicle verification

- `POST /api/v1/listings/{listing_id}/ownership-verification/start` accepts transient normalized
  registration plus VIN/chassis material, requires a current verified identity and optimistic
  listing version, and idempotently starts/resumes provider-hosted capture.
- `GET /api/v1/listings/{listing_id}/ownership-verification/status` returns only allowlisted state
  to the active personal owner. Cross-owner access is `404`; dealer listings return the stable
  unsupported-policy error.
- Canonical identifiers use versioned HMAC-SHA256. Raw registration/VIN/chassis values and capture
  URLs are never persisted, audited, emitted, or returned by status.
- Provider calls run outside database transactions. Duplicate results are harmless, conflicting
  terminal results fail safely, and identity/canonical-version drift makes results stale.
- `WHEELMATCH_VEHICLE_IDENTITY_NORMALIZER`, `WHEELMATCH_VEHICLE_IDENTITY_HMAC_KEY`,
  `WHEELMATCH_VEHICLE_IDENTITY_HASH_VERSION`, and
  `WHEELMATCH_OWNERSHIP_VERIFICATION_PROVIDER` configure the local/test boundary. Disabled and
  deterministic adapters fail closed outside local/test. Production provider and normalizer
  contracts remain undecided.

## Phase 3 Slice 2 identity verification

- `POST /api/v1/me/identity-verifications` idempotently starts or resumes one provider-hosted
  attempt. The capture URL is returned only by this response and is never persisted, audited, or
  emitted.
- `GET /api/v1/me/identity-verification` exposes only the caller's effective status, assurance,
  lifecycle timestamps, projection version, and safe failure code.
- Provider session creation runs between two short transactions. Provider-result application
  atomically finalizes the attempt, projection, audit entry, and allowlisted outbox event.
- Duplicate result IDs are idempotent; conflicting terminal results fail safely; superseded
  attempts cannot replace the current projection.
- Disabled and deterministic identity-verification providers are local/test-only. Staging and
  production fail closed until a real provider and signed callback contract are accepted. Select
  the local/test adapter with `WHEELMATCH_IDENTITY_VERIFICATION_PROVIDER=deterministic`.

## Phase 3 Slice 1 private media processing

- `media.processing.requested` is claimed by `media_id + processing_version` in a short database
  transaction; S3 download, scanner work, decoding, and derivative upload run outside it.
- JPEG/PNG/WebP inputs are signature/MIME/size/checksum checked, resource-bounded, oriented,
  decoded, and re-encoded as bounded private JPEG derivatives with metadata removed.
- Versioned processing evidence stores safe outcomes, sanitized SHA-256, and an average perceptual
  hash. Owner status responses and events omit keys, URLs, hashes, metadata, and scanner details.
- `moderation_pending` means sanitization completed only. It is not moderation approval,
  publication readiness, public visibility, or a CDN contract.
- The disabled and deterministic malware-scanner adapters are development/test-only. Staging and
  production configuration fails closed until a production provider is selected.

## Phase 2 private inventory

- `app/modules/catalogue`: bounded make/model/variant browse and normalized search plus the
  keyed-only canonical vehicle foundation.
- `app/modules/listings`: exactly-one-owner drafts, explicit personal/dealer operating context,
  resumable typed specifications, optimistic versions, and signed owner-query cursors.
- `app/modules/locations`: private geography points and server-side distance predicates; personal
  responses contain locality/coarse area only. Verified published dealer addresses are separate.
- `app/modules/media`: ownership-bound, idempotent, expiring private S3 quarantine intents and
  completion/status/removal state. Completion emits processing work; no moderation decision or
  publication state is implied.

## Phase 1 authentication

- `app/modules/identity`: registration, email/phone challenge state, login, rotating refresh
  families, logout, recovery, session listing/revocation, and authentication rate limits.
- `app/modules/profiles`: current-user profile and optional seller-readiness projection.
- `app/modules/dealers`: organizations, invitations, membership lifecycle, and role changes.
- `app/modules/authorization`: deny-by-default permissions and versioned Redis projections.
- `app/modules/audit`: redacted append-only audit records.

Access tokens are signed and short lived. Opaque refresh tokens rotate on every use; only keyed
hashes are stored, and replay revokes the complete family. Bearer headers and JSON token bodies
are used rather than authentication cookies. Identity/recovery/invitation delivery adapters are
intentionally provider-neutral and default to no-op local adapters.

## Local setup

1. Copy `.env.example` to `.env` and replace local-only placeholders.
2. Create a virtual environment and install the locked dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --require-hashes -r backend\requirements-dev.lock
.\.venv\Scripts\python -m pip install --no-deps -e backend
```

3. Start Docker Desktop, then start dependencies and the API:

```powershell
docker compose --env-file .env up --build api worker outbox-relay
```

The API depends on a one-shot migration job; migrations apply before API and worker startup.

## Runtime entry points

```powershell
.\.venv\Scripts\uvicorn app.main:app --app-dir backend --reload
.\.venv\Scripts\python -m app.workers.outbox_relay
.\.venv\Scripts\python -m app.workers.main
```

## Validation

```powershell
.\.venv\Scripts\ruff format --check backend
.\.venv\Scripts\ruff check backend
.\.venv\Scripts\mypy --config-file backend\pyproject.toml backend
.\.venv\Scripts\pytest backend\tests\unit
powershell -File backend\scripts\run-integration.ps1 -EnvFile .env -TestPath backend\tests -Coverage
.\.venv\Scripts\python backend\scripts\export_openapi.py --check
docker compose --env-file .env config --quiet
docker compose --env-file .env run --rm migrate
docker compose --env-file .env run --rm migrate alembic -c alembic.ini check
docker build -f backend\Dockerfile backend
```

Integration tests require `WHEELMATCH_TEST_DATABASE_URL` and
`WHEELMATCH_TEST_REDIS_URL`. They skip rather than silently using SQLite.

The runtime and development locks are generated from `pyproject.toml`; do not hand-edit them:

```powershell
python -m pip install "pip-tools>=7.5,<8"
python -m piptools compile --generate-hashes --output-file backend\requirements.lock backend\pyproject.toml
python -m piptools compile --extra dev --generate-hashes --output-file backend\requirements-dev.lock backend\pyproject.toml
```

## Local infrastructure strategy

- PostgreSQL/PostGIS and Redis run through Docker Compose.
- LocalStack provides local SQS and S3-compatible AWS APIs.
- Production uses managed RDS/PostGIS, ElastiCache, SQS, S3, Secrets Manager, and KMS.
