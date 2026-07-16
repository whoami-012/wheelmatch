# Mobile architecture

## Goals

- One Flutter codebase for Android-first delivery and later iOS release.
- Feature isolation, testable state transitions, explicit error states, and safe retry.
- No client-side reimplementation of authorization, publication readiness, interest eligibility, or ranking policy.

## Planned structure

```text
mobile/
  lib/
    app/
      app.dart
      router.dart
      bootstrap.dart
    core/
      auth/
      config/
      errors/
      networking/
      realtime/
      storage/
      telemetry/
      ui/
    features/
      identity/
      profile/
      seller_onboarding/
      dealer/
      listings/
      media/
      discovery/
      preferences/
      saved_listings/
      interest_requests/
      matches/
      conversations/
      notifications/
      reports/
      settings/
    shared/
      models/
      widgets/
  test/
  integration_test/
```

Each feature uses:

```text
feature/
  presentation/   screens, widgets, route bindings
  application/    Riverpod controllers and use-case orchestration
  domain/         immutable feature models and local rules
  data/           API DTOs, repositories, mappers
```

Do not force four layers into trivial components. Cross-feature access goes through public providers/repositories, not private feature files.

## State management with Riverpod

- Use providers for dependency injection and immutable state exposure.
- Use AsyncNotifier or equivalent for server-backed commands and queries.
- Model loading, data, empty, stale, and failure explicitly.
- Keep transient widget state local when it has no domain meaning.
- Scope feed sessions, listing drafts, and conversation subscriptions to routes.
- Invalidate providers after authoritative API responses; do not infer server state from optimistic UI alone.
- Generate immutable DTO/model code only after the scaffold selects the generation tooling.

### Optimistic actions

| Action | Client behavior | Reconciliation |
|---|---|---|
| Swipe reaction | Remove current card immediately | Restore or show retry if API fails |
| Save listing | Optimistic icon update | Replace with server state |
| Send message | Local pending bubble with client message ID | Replace on committed message acknowledgement |
| Withdraw interest | No optimistic terminal transition | Wait for server because withdrawal is irreversible |
| Listing publication | Never optimistic | Render backend readiness and publication result |

## Navigation with GoRouter

Route groups:

- Unauthenticated: welcome, sign-in, registration, verification, recovery.
- Authenticated: discovery, search, saved, sell, requests, chats, settings.
- Seller onboarding: seller profile, identity verification, ownership verification, publication checklist.
- Dealer: organization selector, inventory, metadata-only inbox, assignment.
- Moderation/admin is not part of the consumer mobile MVP unless explicitly approved.

Route guards use local session state only for navigation convenience. The API remains authoritative. Deep links must re-fetch the resource and handle unavailable, unauthorized, or deleted states without leaking existence.

## Networking with Dio

Interceptors should add:

- Correlation/request ID.
- Locale and supported application version.
- Access token.
- Idempotency key supplied by command controllers.
- Conditional ETag or resource version where supported.

Requirements:

- Serialize refresh attempts so concurrent 401 responses cause one rotation.
- Retry safe reads and explicitly idempotent commands only.
- Never retry a mutation with a new idempotency key.
- Map stable API problem codes to typed application failures.
- Apply connect, receive, and send timeouts; file uploads use a separate policy.
- Redact authorization, cookies, private coordinates, documents, message bodies, and upload URLs from logs and Sentry.

## Realtime client

1. Obtain a short-lived single-use WebSocket ticket from REST.
2. Connect and authenticate.
3. Subscribe to authorized conversation IDs individually.
4. Track connection generation and last received event cursor.
5. On reconnect, fetch missed messages/events using REST cursor pagination.
6. Treat message acknowledgement as authoritative only after server commit.
7. Handle assignment revocation by immediately closing the conversation screen and clearing cached message bodies.

WebSocket events are hints; REST/PostgreSQL state resolves conflicts.

## Discovery UX

- Keep a bounded prefetch window rather than loading an entire feed.
- Store the opaque next cursor and feed session ID without decoding them.
- Remove Not Interested cards immediately and maintain an in-memory exclusion set for prefetched results.
- Reaction management screens use server pagination and server status filters.
- Personal listings show locality, distance band, and coarse area only; never render a derived seller pin.
- Dealer exact pins render only when the API returns public_business visibility.

See [discovery and preferences](discovery-and-preferences.md).

## Listing and media UX

- Persist resumable server drafts; local draft storage is a convenience, not the source of truth.
- Request upload intents, upload directly to S3, and confirm completion.
- Show per-file scan/processing state and do not allow publication while processing.
- Personal location selection explains that exact coordinates are private.
- First publication progressively collects missing seller, identity, and ownership requirements.
- Dealer members select Sell as Personal or Sell as Organization per draft.

The local persistence package is not confirmed. Select it during scaffold based on encrypted data and migration requirements; do not persist verification documents or exact location history locally.

## Platform compatibility

Android launches first, but feature code must avoid Android-only APIs. Wrap permissions, secure storage, push, camera/gallery, and deep-link behavior behind platform adapters. Maintain:

- Android and iOS permission descriptions.
- FCM token registration and rotation on both platforms.
- APNs configuration through FCM for iOS.
- Platform-specific integration tests.
- Accessible semantics, dynamic text support, keyboard/focus behavior, and reduced-motion compatibility.

## Mobile validation

Planned gates after scaffold:

```powershell
flutter pub get
dart format --output=none --set-exit-if-changed .
flutter analyze
flutter test
flutter test integration_test
flutter build apk --release
```

These commands are planned, not currently repository-verified. Add an iOS build gate on macOS before iOS release.
