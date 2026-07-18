# Database design

PostgreSQL with PostGIS is the source of truth. Use UUIDv7 primary keys, UTC timestamptz, lower-case snake_case names, explicit foreign keys, and constrained enums/reference tables.

## Identity and organization

| Table | Key columns and constraints |
|---|---|
| users | id PK; normalized email/phone partial unique; account status; verification timestamps; deleted_at |
| profiles | user_id PK/FK; display name, avatar reference, coarse home/search locality |
| seller_profiles | user_id PK/FK; status, verification projection, activated_at |
| verification_challenges | id PK; user/channel; keyed code hash; bounded attempts; expires/consumed timestamps |
| password_recovery_challenges | id PK; user; unique keyed token hash; bounded attempts; expires/consumed timestamps |
| session_families | id PK; user; non-invasive device metadata; expires/revoked/reuse-detected timestamps |
| refresh_sessions | id PK; family and parent token; unique keyed token hash; expires/used/revoked timestamps |
| rate_limit_buckets | scope/subject unique; keyed subject hash; window count and expiry; Redis fallback authority |
| dealer_organizations | id PK; legal/display names; status; verification status |
| dealer_memberships | organization_id, user_id unique; role; status; lifecycle timestamps |
| audit_logs | append-only actor/context/action/resource/redacted change/trace records |
| dealer_public_addresses | Phase 2 target: organization exact point; verification and publication states |

Dealer roles are owner, admin, inventory_manager, and sales_agent. Membership rows are retained after leaving or revocation.

Migration `0002_identity_authorization` implements the Phase 1 rows above with UUIDv7 application-generated primary keys, UTC timestamps, explicit lifecycle checks, lookup/expiry indexes, partial unique normalized identities, and a unique organization/user membership. Passwords, refresh tokens, verification codes, recovery tokens, and invitation tokens are never stored in plaintext. PostGIS-owned metadata tables remain excluded from Alembic autogeneration.

## Catalogue, vehicle, listing, and location

| Table | Purpose |
|---|---|
| vehicle_makes/models/variants | Controlled searchable taxonomy with unique parent/name keys |
| canonical_vehicles | Deduplicated vehicle identity and HMAC identifiers |
| vehicle_specs | Shared manufacture/registration year, odometer, fuel, transmission, ownership, colour, condition |
| car_specs | Body type, seats, engine, drivetrain, emission fields |
| bike_specs | Bike category, engine, start, braking fields |
| listings | Owner, canonical vehicle, price, description, lifecycle/publication/moderation states, version |
| listing_locations | Private exact_point, public area, internal coarse cell, visibility and optional public address |
| listing_media | Private object keys, sanitized derivative, checksum, perceptual hash, MIME, scan/moderation states, order |
| listing_price_history | Append-only old/new price and actor |
| listing_status_history | Append-only state transitions and reasons |
| listing_publication_evidence | Exact listing/media/verification/moderation/policy versions used for publication |

Migration `0003_phase2_core` implements the controlled make/model/variant hierarchy, keyed-only
canonical vehicle foundation, private listings, shared/car/bike specification tables,
`dealer_public_addresses`, PostGIS `listing_locations`, and `listing_media`. Listing rows enforce
exactly one owner, positive optimistic versions, private draft lifecycle state, creator
attribution, and indexed personal/organization owner queries. Media rows enforce bounded order,
positive size/version, constrained lifecycle state, unique backend object keys, and one active
media row per listing/order. Publication evidence and Phase 3 lifecycle states remain targets.

### Exactly one owner

```sql
CHECK (
  (
    owner_type = 'user'
    AND owner_user_id IS NOT NULL
    AND owner_organization_id IS NULL
  )
  OR
  (
    owner_type = 'dealer_organization'
    AND owner_user_id IS NULL
    AND owner_organization_id IS NOT NULL
  )
)
```

created_by_user_id records the actor; it does not grant ownership.

### Location constraints and indexes

```sql
CREATE INDEX ix_listing_locations_exact_point
ON listing_locations USING GIST (exact_point);

CHECK (
  (visibility = 'approximate' AND public_address_id IS NULL)
  OR
  (visibility = 'public_business' AND public_address_id IS NOT NULL)
);
```

The API never serializes exact_point for personal listings.

### Duplicate public personal listing

```sql
CREATE UNIQUE INDEX uq_live_personal_listing_per_vehicle
ON listings (canonical_vehicle_id)
WHERE owner_type = 'user'
  AND publication_status = 'published'
  AND lifecycle_status IN ('available', 'reserved');
```

Publication also locks the canonical vehicle and checks conflicts transactionally.

## Verification and moderation

| Table | Key design |
|---|---|
| listing_media | Private quarantine state, processing version, safe terminal failure code |
| media_processing_evidence | One versioned claim/outcome per media and processing version; bounded lease, sanitized hashes, safe evidence |
| media_derivatives | Private generated key, kind, dimensions, bytes and SHA-256; unique per media/version/kind |
| identity_verifications | Append-only user attempts, provider reference, status, verified/expires/revoked, reviewer |
| user_verification_states | Effective current identity projection and version |
| vehicle_ownership_verifications | Owner–canonical-vehicle verification, 180-day default freshness, material fingerprint |
| listing_ownership_checks | New audit record for every publication/republication; reused flag |
| moderation_cases | Subject, priority, risk score, assignment, SLA and disposition |
| moderation_decisions | Case, listing version, rule/provider version, actor and result |
| verification_document_refs | Isolated encrypted provider/object references and retention date; no public content |

`canonical_vehicles` stores registration and VIN/chassis identity only as jurisdiction-normalized,
versioned keyed HMACs. Phase 3 Slice 3 stores no raw or encrypted identifier originals.

Migration `0005_phase3_identity_verification` implements append-only user attempt history and the
effective projection. Attempts have monotonic per-user numbers, private provider reference and
unique result ID constraints, constrained status/assurance/evidence, user/status/created ordering,
and a partial unique index across `session_pending`, `pending`, and `manual_review`. The projection
has one row per user, a current-attempt foreign key, monotonic version, and constraints preventing
`verified` without assurance plus valid verified/expiry timestamps. Neither table stores capture
URLs, identity documents, legal names, birth dates, document numbers, provider payloads, scores,
or raw evidence.

Migration `0006_phase3_vehicle_ownership` adds positive canonical `identity_version`,
`vehicle_ownership_verifications`, and `verification_document_refs`. Ownership rows bind the
personal owner, listing, canonical vehicle, identity attempt/projection version, vehicle identity
and hash versions, ownership basis, keyed material fingerprint, private provider correlation, safe
lifecycle state, and timestamps. Unique provider reference/result constraints make replay
deterministic; owner/canonical attempt numbers preserve history; a partial unique index permits at
most one unresolved non-superseded owner/canonical attempt. Document references contain opaque
provider/object references and retention metadata only—never bytes, media keys, identifiers,
payloads, or evidence. `listing_ownership_checks` remains a later publication-slice target and is
not created by Slice 3.

Migration `0007_phase3_listing_submit` adds constrained private/pending publication and
not-started/pending moderation state to `listings`, plus the submitted listing version/timestamp.
`listing_submission_attempts` records monotonic attempts per listing, one active attempt per
listing/version, personal actor/owner context, safe submission state, bound identity/ownership
versions, internal ownership/media fingerprints, policy version, a bounded non-authoritative safe
code array, and submitted/superseded timestamps. Current readiness is always recomputed from source
tables; attempt evidence cannot authorize publication. Slice 4 adds no ownership-check,
publication-evidence, moderation-case/decision, public-index, or published-state table.

Migration `0008_phase3_ownership_reuse` adds constrained `canonical_vehicles.identity_status`
(`active`, `disputed`, `transferred`, `stolen`, `written_off`, or `fraud_review`) and adds
`ownership_reused`, nullable positive `ownership_reuse_policy_version`, and nullable immutable
`ownership_effective_expires_at` to `listing_submission_attempts`. Existing attempts migrate as
non-reused. A reused attempt must reference an ownership verification and contain a positive
policy version plus effective expiry; non-reused attempts cannot retain reuse-only fields. The
migration does not alter ownership verification timestamps and does not create
`listing_ownership_checks`.

Phase 3 Slice 1 keeps processing evidence and private derivative addressing out of API projections.
`media_processing_evidence(media_id, processing_version)` and
`media_derivatives(media_id, processing_version, kind)` are unique. Final media state, evidence,
derivative rows, audit, and allowlisted outbox event share one transaction.

## Reactions and preferences

| Table | Key design |
|---|---|
| listing_reactions | user/listing unique; interested or not_interested; version and timestamps |
| listing_reaction_events | Append-only previous/new reaction, source, idempotency key |
| saved_listings | user/listing primary key |
| user_discovery_preferences | Typed price, year, radius, private search origin, version |
| preferred_* join tables | Vehicle types, makes, models, body, fuel, transmission |
| hidden_sellers | user/seller unique; discovery only |
| blocked_users | blocker/blocked unique; safety boundary |
| inferred_preference_profiles | user/generation/ranker version |
| inferred_preference_features | user/generation/feature type/key unique |
| feed_impressions | user/listing first/last exposure and bounded count |

Neutral reaction means no listing_reactions row.

## Interest, match, and messaging

| Table | Key design |
|---|---|
| interest_request_threads | buyer/listing unique locking boundary; seller owner entity snapshot; attempt_count 0–2; closure state/reason |
| interest_requests | thread, attempt 1–2, predecessor, status, terminal reason, next_retry_at |
| matches | interest_request_id unique; buyer, seller entity, listing, status |
| conversations | match_id unique; seller entity; assignment and communication states; authorization_version |
| conversation_assignments | Append-only organization, assigned user/membership, assigning actor, start/end and reason |
| conversation_members | Conversation/user role and read cursor |
| messages | Conversation, actual sender user/membership/assignment context, client_message_id |
| conversation_events | Neutral assignment/read-only/system events |
| message_receipts | Message/user delivered/read timestamps |
| conversation_location_shares | Explicit post-match meetup point/address, expiry and revocation |

### Request constraints

```sql
CREATE UNIQUE INDEX uq_interest_request_pending_per_thread
ON interest_requests (thread_id)
WHERE status = 'pending';

ALTER TABLE interest_requests
  ADD CONSTRAINT uq_interest_request_attempt
  UNIQUE (thread_id, attempt_number),
  ADD CONSTRAINT uq_interest_request_successor
  UNIQUE (previous_request_id),
  ADD CONSTRAINT ck_interest_request_attempt
  CHECK (attempt_number IN (1, 2)),
  ADD CONSTRAINT ck_interest_request_predecessor
  CHECK (
    (attempt_number = 1 AND previous_request_id IS NULL)
    OR
    (attempt_number = 2 AND previous_request_id IS NOT NULL)
  );
```

Cross-row cooldown and predecessor rules remain transactional.

### Active dealer assignment

```sql
CREATE UNIQUE INDEX uq_active_conversation_assignment
ON conversation_assignments (conversation_id)
WHERE ended_at IS NULL;
```

ASSIGNED conversations have one active row; UNASSIGNED conversations have none.

### Message idempotency

```sql
CREATE UNIQUE INDEX uq_message_client_id
ON messages (conversation_id, sender_user_id, client_message_id);
```

## Notifications, reports, audit, and reliability

| Table | Purpose |
|---|---|
| notifications | User, category, channel, template, status, dedupe key, provider reference |
| notification_preferences | Per category/channel and quiet hours |
| device_tokens | User/device/provider token encrypted, last_seen, revoked |
| reports | Reporter, subject type/id, reason, evidence reference, state |
| audit_logs | Append-only actor/context/action/resource/redacted diff/trace |
| outbox_events | Versioned event, aggregate, payload, availability, publication attempts |
| consumer_events | Consumer/event unique idempotency record |
| idempotency_keys | Actor/operation/key unique; request hash and response reference |
| automation_executions | Workflow/event/stage/status/attempt and redacted failure |
| search_subscriptions | Saved filters, cadence and next evaluation time |

## Search indexes

- GIN tsvector over make/model/variant and approved description.
- pg_trgm indexes for controlled fuzzy make/model text.
- B-tree partial indexes on published/available/moderation-approved listings.
- B-tree listing owner/status/update indexes.
- GiST listing exact_point.
- Reaction, saved, interest, conversation, notification, and audit keyset indexes ending in unique ID.
- Perceptual hash/fingerprint lookup for duplicate media and vehicles.

## Deletion and retention

Use lifecycle state for business removal and deleted_at only for deletion workflows. Audit, verification links, request attempts, assignment history, price history, and publication evidence are append-only and retained under policy. Anonymize user-facing content when privacy deletion conflicts with fraud/legal retention.

Privacy durations are defined in [security threat model](security-threat-model.md).
