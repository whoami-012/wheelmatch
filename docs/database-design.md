# Database design

PostgreSQL with PostGIS is the source of truth. Use UUIDv7 primary keys, UTC timestamptz, lower-case snake_case names, explicit foreign keys, and constrained enums/reference tables.

## Identity and organization

| Table | Key columns and constraints |
|---|---|
| users | id PK; normalized email/phone partial unique; account status; verification timestamps; deleted_at |
| profiles | user_id PK/FK; display name, avatar reference, coarse home/search locality |
| seller_profiles | user_id PK/FK; status, verification projection, activated_at |
| refresh_sessions | id PK; user_id; hashed token family; device; expires/revoked/reuse_detected timestamps |
| dealer_organizations | id PK; legal/display names; status; verification status |
| dealer_memberships | organization_id, user_id unique; role; status; lifecycle timestamps |
| dealer_public_addresses | organization_id; exact_point; verification and publication states |

Dealer roles are owner, admin, inventory_manager, and sales_agent. Membership rows are retained after leaving or revocation.

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
| identity_verifications | Append-only user attempts, provider reference, status, verified/expires/revoked, reviewer |
| user_verification_states | Effective current identity projection and version |
| vehicle_ownership_verifications | Owner–canonical-vehicle verification, 180-day default freshness, material fingerprint |
| listing_ownership_checks | New audit record for every publication/republication; reused flag |
| moderation_cases | Subject, priority, risk score, assignment, SLA and disposition |
| moderation_decisions | Case, listing version, rule/provider version, actor and result |
| verification_document_refs | Isolated encrypted provider/object references and retention date; no public content |

canonical_vehicles stores registration and VIN/chassis identity as jurisdiction-normalized keyed HMACs with hash_version. Encrypted originals exist only when operationally necessary.

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
