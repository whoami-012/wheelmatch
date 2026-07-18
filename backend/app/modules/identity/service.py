from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import IdempotencyConflictError, IdempotencyRepository
from app.core.ids import uuid7
from app.core.outbox import enqueue_event
from app.core.security import (
    PasswordService,
    SecretHasher,
    generate_opaque_token,
    generate_verification_code,
    normalize_email,
    normalize_phone,
)
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.authorization.cache import AuthorizationCache
from app.modules.identity.delivery import IdentityChallengeDelivery
from app.modules.identity.models import PasswordRecoveryChallenge, User, VerificationChallenge
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.schemas import (
    RecoveryAcceptedResponse,
    RegisterRequest,
    RegistrationResponse,
    VerificationDispatchResponse,
    VerificationResponse,
)
from app.modules.profiles.models import Profile


@dataclass(frozen=True, slots=True)
class _VerificationDispatch:
    user_id: UUID
    kind: str
    destination: str
    challenge_id: UUID
    code: str


class IdentityService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        password_service: PasswordService,
        secret_hasher: SecretHasher,
        audit: AuditRecorder,
        delivery: IdentityChallengeDelivery,
        authorization_cache: AuthorizationCache,
        idempotency_repository: IdempotencyRepository,
        verification_ttl_seconds: int,
        recovery_ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._passwords = password_service
        self._secrets = secret_hasher
        self._audit = audit
        self._delivery = delivery
        self._authorization_cache = authorization_cache
        self._idempotency = idempotency_repository
        self._verification_ttl = timedelta(seconds=verification_ttl_seconds)
        self._recovery_ttl = timedelta(seconds=recovery_ttl_seconds)

    async def register(
        self,
        session: AsyncSession,
        request: RegisterRequest,
        *,
        idempotency_key: str,
        request_hash: str,
    ) -> RegistrationResponse:
        try:
            normalized_email = normalize_email(request.email)
            normalized_phone = normalize_phone(request.phone) if request.phone else None
            password_hash = await asyncio.to_thread(
                self._passwords.hash,
                request.password.get_secret_value(),
                normalized_email=normalized_email,
            )
        except ValueError as exc:
            raise AppError(
                status=422,
                code="REGISTRATION_INPUT_INVALID",
                title="Registration data is invalid",
                detail=str(exc),
            ) from exc

        now = datetime.now(UTC)
        user = User(
            id=uuid7(),
            normalized_email=normalized_email,
            normalized_phone=normalized_phone,
            password_hash=password_hash,
            status="active",
            password_changed_at=now,
        )
        dispatches: list[_VerificationDispatch] = []
        response: RegistrationResponse | None = None

        try:
            async with session.begin():
                try:
                    reservation = await self._idempotency.reserve(
                        session,
                        scope="public",
                        operation="identity.register",
                        key=idempotency_key,
                        request_hash=request_hash,
                        expires_at=now + timedelta(hours=24),
                    )
                except IdempotencyConflictError as exc:
                    raise AppError(
                        status=409,
                        code="IDEMPOTENCY_KEY_CONFLICT",
                        title="Idempotency key conflicts with an earlier request",
                    ) from exc
                if not reservation.acquired:
                    if reservation.replay_body is None:
                        raise AppError(
                            status=409,
                            code="IDEMPOTENCY_IN_PROGRESS",
                            title="An idempotent request is already in progress",
                        )
                    return RegistrationResponse.model_validate(reservation.replay_body)
                existing = await self._repository.get_user_by_email(session, normalized_email)
                if existing is not None:
                    raise AppError(
                        status=409,
                        code="REGISTRATION_UNAVAILABLE",
                        title="Registration is unavailable",
                        detail="An account cannot be created with the supplied identity.",
                    )
                session.add(user)
                await session.flush()
                session.add(Profile(user_id=user.id, display_name=request.display_name))
                dispatches.append(
                    self._new_verification_challenge(
                        session,
                        user=user,
                        kind="email",
                        destination=normalized_email,
                        now=now,
                    )
                )
                if normalized_phone is not None:
                    dispatches.append(
                        self._new_verification_challenge(
                            session,
                            user=user,
                            kind="phone",
                            destination=normalized_phone,
                            now=now,
                        )
                    )
                self._audit.record(
                    session,
                    action="identity.user.registered",
                    outcome="success",
                    resource_type="user",
                    actor_user_id=user.id,
                    resource_id=user.id,
                    changes={"status": "active", "phone_supplied": normalized_phone is not None},
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="identity.user.registered",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    payload={"verification_channels": [item.kind for item in dispatches]},
                )
                response = RegistrationResponse(
                    user_id=user.id,
                    status=user.status,
                    verification_challenges=[
                        VerificationDispatchResponse(kind=item.kind, challenge_id=item.challenge_id)
                        for item in dispatches
                    ],
                )
                await self._idempotency.complete(
                    session,
                    scope="public",
                    operation="identity.register",
                    key=idempotency_key,
                    response_status=201,
                    response_body=response.model_dump(mode="json"),
                    resource_type="user",
                    resource_id=user.id,
                )
                await session.flush()
        except IntegrityError as exc:
            raise AppError(
                status=409,
                code="REGISTRATION_UNAVAILABLE",
                title="Registration is unavailable",
                detail="An account cannot be created with the supplied identity.",
            ) from exc

        for dispatch in dispatches:
            await self._delivery.send_verification(
                user_id=dispatch.user_id,
                kind=dispatch.kind,
                destination=dispatch.destination,
                challenge_id=dispatch.challenge_id,
                code=dispatch.code,
            )

        if response is None:
            raise AssertionError("registration completed without a response")
        return response

    def _new_verification_challenge(
        self,
        session: AsyncSession,
        *,
        user: User,
        kind: str,
        destination: str,
        now: datetime,
    ) -> _VerificationDispatch:
        code = generate_verification_code()
        challenge = VerificationChallenge(
            id=uuid7(),
            user_id=user.id,
            kind=kind,
            secret_hash=self._secrets.digest(code),
            max_attempts=5,
            expires_at=now + self._verification_ttl,
        )
        session.add(challenge)
        enqueue_event(
            session,
            event_type=f"identity.{kind}_verification.requested",
            aggregate_type="user",
            aggregate_id=user.id,
            payload={"challenge_id": str(challenge.id), "channel": kind},
        )
        return _VerificationDispatch(
            user_id=user.id,
            kind=kind,
            destination=destination,
            challenge_id=challenge.id,
            code=code,
        )

    async def verify_challenge(
        self,
        session: AsyncSession,
        *,
        challenge_id: UUID,
        code: str,
        expected_kind: str,
    ) -> VerificationResponse:
        now = datetime.now(UTC)
        verified_user_id: UUID | None = None
        failed = False
        async with session.begin():
            challenge = await self._repository.get_verification_challenge(
                session, challenge_id, for_update=True
            )
            if (
                challenge is None
                or challenge.kind != expected_kind
                or challenge.consumed_at is not None
                or challenge.expires_at <= now
                or challenge.attempt_count >= challenge.max_attempts
            ):
                failed = True
            elif not self._secrets.verify(code, challenge.secret_hash):
                challenge.attempt_count += 1
                failed = True
                self._audit.record(
                    session,
                    action=f"identity.{expected_kind}_verification.failed",
                    outcome="denied",
                    reason_code="INVALID_CHALLENGE",
                    resource_type="user",
                    actor_user_id=challenge.user_id,
                    resource_id=challenge.user_id,
                    changes={"attempt_count": challenge.attempt_count},
                    request_id=get_request_id(),
                )
            else:
                user = await self._repository.get_user_by_id(
                    session, challenge.user_id, for_update=True
                )
                if user is None or user.status == "deleted":
                    failed = True
                else:
                    challenge.consumed_at = now
                    if expected_kind == "email":
                        user.email_verified_at = now
                    else:
                        user.phone_verified_at = now
                    user.authorization_version += 1
                    verified_user_id = user.id
                    self._audit.record(
                        session,
                        action=f"identity.{expected_kind}_verified",
                        outcome="success",
                        resource_type="user",
                        actor_user_id=user.id,
                        resource_id=user.id,
                        changes={f"{expected_kind}_verified": True},
                        request_id=get_request_id(),
                    )
                    enqueue_event(
                        session,
                        event_type=f"identity.{expected_kind}_verified",
                        aggregate_type="user",
                        aggregate_id=user.id,
                        payload={"authorization_version": user.authorization_version},
                    )

        if failed or verified_user_id is None:
            raise AppError(
                status=400,
                code="VERIFICATION_FAILED",
                title="Verification failed",
                detail="The verification challenge is invalid or no longer usable.",
            )
        await self._authorization_cache.invalidate([verified_user_id])
        return VerificationResponse(verified=True, kind=expected_kind)

    async def request_recovery(
        self, session: AsyncSession, *, email: str
    ) -> RecoveryAcceptedResponse:
        try:
            normalized_email = normalize_email(email)
        except ValueError:
            return RecoveryAcceptedResponse()

        token = generate_opaque_token()
        now = datetime.now(UTC)
        challenge: PasswordRecoveryChallenge | None = None
        user: User | None = None
        async with session.begin():
            user = await self._repository.get_user_by_email(
                session, normalized_email, for_update=True
            )
            if user is not None and user.status != "deleted":
                await self._repository.consume_open_recovery_challenges(
                    session, user_id=user.id, consumed_at=now
                )
                challenge = PasswordRecoveryChallenge(
                    id=uuid7(),
                    user_id=user.id,
                    token_hash=self._secrets.digest(token),
                    expires_at=now + self._recovery_ttl,
                )
                session.add(challenge)
                self._audit.record(
                    session,
                    action="identity.password_recovery.requested",
                    outcome="accepted",
                    resource_type="user",
                    actor_user_id=user.id,
                    resource_id=user.id,
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="identity.password_recovery.requested",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    payload={"challenge_id": str(challenge.id)},
                )

        if user is not None and challenge is not None:
            await self._delivery.send_recovery(
                user_id=user.id,
                destination=user.normalized_email,
                challenge_id=challenge.id,
                token=token,
            )
        return RecoveryAcceptedResponse()

    async def reset_password(self, session: AsyncSession, *, token: str, new_password: str) -> None:
        try:
            password_hash = await asyncio.to_thread(self._passwords.hash, new_password)
        except ValueError as exc:
            raise AppError(
                status=422,
                code="PASSWORD_POLICY_FAILED",
                title="Password does not meet policy",
                detail=str(exc),
            ) from exc

        now = datetime.now(UTC)
        token_hash = self._secrets.digest(token)
        changed_user_id: UUID | None = None
        async with session.begin():
            challenge = await self._repository.get_recovery_by_hash(
                session, token_hash, for_update=True
            )
            if (
                challenge is None
                or not self._secrets.verify(token, token_hash)
                or challenge.consumed_at is not None
                or challenge.expires_at <= now
                or challenge.attempt_count >= challenge.max_attempts
            ):
                self._secrets.verify(token, "0" * 64)
            else:
                user = await self._repository.get_user_by_id(
                    session, challenge.user_id, for_update=True
                )
                if user is not None and user.status != "deleted":
                    challenge.consumed_at = now
                    user.password_hash = password_hash
                    user.password_changed_at = now
                    user.failed_login_attempts = 0
                    user.login_locked_until = None
                    user.authorization_version += 1
                    changed_user_id = user.id
                    await self._repository.revoke_user_sessions(
                        session,
                        user_id=user.id,
                        revoked_at=now,
                        reason="password_recovery",
                    )
                    self._audit.record(
                        session,
                        action="identity.password_recovery.completed",
                        outcome="success",
                        resource_type="user",
                        actor_user_id=user.id,
                        resource_id=user.id,
                        request_id=get_request_id(),
                    )
                    enqueue_event(
                        session,
                        event_type="identity.password.changed",
                        aggregate_type="user",
                        aggregate_id=user.id,
                        payload={"reason": "recovery", "sessions_revoked": True},
                    )

        if changed_user_id is None:
            raise AppError(
                status=400,
                code="RECOVERY_FAILED",
                title="Recovery failed",
                detail="The recovery credential is invalid or no longer usable.",
            )
        await self._authorization_cache.invalidate([changed_user_id])
