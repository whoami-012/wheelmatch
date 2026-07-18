from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Never
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.ids import uuid7
from app.core.outbox import enqueue_event
from app.core.security import (
    AccessTokenError,
    AccessTokenService,
    PasswordService,
    SecretHasher,
    generate_opaque_token,
    normalize_email,
)
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.authorization.cache import AuthorizationCache
from app.modules.identity.models import RefreshSession, SessionFamily
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.schemas import LoginRequest, SessionResponse, TokenResponse


@dataclass(frozen=True, slots=True)
class CurrentPrincipal:
    user_id: UUID
    session_family_id: UUID
    token_id: UUID


class SessionService:
    def __init__(
        self,
        *,
        repository: IdentityRepository,
        password_service: PasswordService,
        secret_hasher: SecretHasher,
        access_tokens: AccessTokenService,
        audit: AuditRecorder,
        authorization_cache: AuthorizationCache,
        refresh_ttl_seconds: int,
        login_failure_threshold: int,
        login_lock_seconds: int,
    ) -> None:
        self._repository = repository
        self._passwords = password_service
        self._secrets = secret_hasher
        self._access_tokens = access_tokens
        self._audit = audit
        self._authorization_cache = authorization_cache
        self._refresh_ttl = timedelta(seconds=refresh_ttl_seconds)
        self._login_failure_threshold = login_failure_threshold
        self._login_lock = timedelta(seconds=login_lock_seconds)

    async def login(self, session: AsyncSession, request: LoginRequest) -> TokenResponse:
        try:
            normalized_email = normalize_email(request.email)
        except ValueError:
            normalized_email = "invalid@example.invalid"

        candidate = await self._repository.get_user_by_email(session, normalized_email)
        candidate_hash = candidate.password_hash if candidate is not None else None
        await session.rollback()
        password = request.password.get_secret_value()
        verified = await asyncio.to_thread(
            self._passwords.verify_or_dummy, candidate_hash, password
        )
        replacement_hash: str | None = None
        if verified and candidate_hash is not None and self._passwords.needs_rehash(candidate_hash):
            replacement_hash = await asyncio.to_thread(
                self._passwords.rehash,
                password,
            )

        now = datetime.now(UTC)
        family: SessionFamily | None = None
        refresh_token: str | None = None
        login_failed = False
        async with session.begin():
            user = await self._repository.get_user_by_email(
                session, normalized_email, for_update=True
            )
            locked = (
                user is not None
                and user.login_locked_until is not None
                and user.login_locked_until > now
            )
            valid = (
                verified
                and user is not None
                and user.password_hash == candidate_hash
                and user.status == "active"
                and not locked
            )
            if not valid:
                login_failed = True
                if user is not None:
                    if not locked:
                        user.failed_login_attempts += 1
                        if user.failed_login_attempts >= self._login_failure_threshold:
                            user.login_locked_until = now + self._login_lock
                    self._audit.record(
                        session,
                        action="identity.login.failed",
                        outcome="denied",
                        reason_code="AUTHENTICATION_FAILED",
                        resource_type="user",
                        actor_user_id=user.id,
                        resource_id=user.id,
                        request_id=get_request_id(),
                    )
            else:
                assert user is not None
                user.failed_login_attempts = 0
                user.login_locked_until = None
                if replacement_hash is not None:
                    user.password_hash = replacement_hash
                refresh_token = generate_opaque_token()
                expires_at = now + self._refresh_ttl
                family = SessionFamily(
                    id=uuid7(),
                    user_id=user.id,
                    device_name=request.device_name,
                    device_platform=request.device_platform,
                    created_at=now,
                    last_used_at=now,
                    expires_at=expires_at,
                )
                session.add(family)
                await session.flush()
                refresh_session = RefreshSession(
                    id=uuid7(),
                    family_id=family.id,
                    token_hash=self._secrets.digest(refresh_token),
                    created_at=now,
                    expires_at=expires_at,
                )
                session.add(refresh_session)
                self._audit.record(
                    session,
                    action="identity.session.created",
                    outcome="success",
                    resource_type="session_family",
                    actor_user_id=user.id,
                    resource_id=family.id,
                    changes={"device_platform": request.device_platform},
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="identity.session.created",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    payload={
                        "session_family_id": str(family.id),
                        "device_platform": request.device_platform,
                    },
                )
                await session.flush()

        if login_failed or family is None or refresh_token is None:
            self._raise_authentication_failed()
        return self._token_response(
            user_id=family.user_id,
            family=family,
            refresh_token=refresh_token,
            now=now,
        )

    async def refresh(self, session: AsyncSession, *, refresh_token: str) -> TokenResponse:
        now = datetime.now(UTC)
        token_hash = self._secrets.digest(refresh_token)
        rotated_token: str | None = None
        family: SessionFamily | None = None
        user_id: UUID | None = None
        replay_detected = False
        invalid = False

        async with session.begin():
            record = await self._repository.get_refresh_session_by_hash(
                session, token_hash, for_update=True
            )
            if record is None:
                self._secrets.verify(refresh_token, "0" * 64)
                invalid = True
            else:
                current, family, user = record
                if current.used_at is not None:
                    replay_detected = True
                    await self._repository.revoke_family(
                        session,
                        family=family,
                        revoked_at=now,
                        reason="refresh_replay",
                        reuse_detected=True,
                    )
                    self._audit.record(
                        session,
                        action="identity.session.refresh_replay_detected",
                        outcome="revoked",
                        reason_code="REFRESH_REPLAY",
                        resource_type="session_family",
                        actor_user_id=user.id,
                        resource_id=family.id,
                        request_id=get_request_id(),
                    )
                    enqueue_event(
                        session,
                        event_type="identity.session.revoked",
                        aggregate_type="user",
                        aggregate_id=user.id,
                        payload={
                            "session_family_id": str(family.id),
                            "reason": "refresh_replay",
                        },
                    )
                    user_id = user.id
                elif (
                    not self._secrets.verify(refresh_token, current.token_hash)
                    or current.revoked_at is not None
                    or current.expires_at <= now
                    or family.revoked_at is not None
                    or family.expires_at <= now
                    or user.status != "active"
                ):
                    invalid = True
                    if user.status != "active" and family.revoked_at is None:
                        await self._repository.revoke_family(
                            session,
                            family=family,
                            revoked_at=now,
                            reason="account_inactive",
                        )
                else:
                    rotated_token = generate_opaque_token()
                    current.used_at = now
                    family.last_used_at = now
                    session.add(
                        RefreshSession(
                            id=uuid7(),
                            family_id=family.id,
                            parent_session_id=current.id,
                            token_hash=self._secrets.digest(rotated_token),
                            created_at=now,
                            expires_at=family.expires_at,
                        )
                    )
                    user_id = user.id
                    self._audit.record(
                        session,
                        action="identity.session.refreshed",
                        outcome="success",
                        resource_type="session_family",
                        actor_user_id=user.id,
                        resource_id=family.id,
                        request_id=get_request_id(),
                    )

        if user_id is not None and replay_detected:
            await self._authorization_cache.invalidate([user_id])
            raise AppError(
                status=401,
                code="SESSION_INVALID",
                title="Authentication is required",
            )
        if invalid or rotated_token is None or family is None or user_id is None:
            self._raise_authentication_failed()
        return self._token_response(
            user_id=user_id,
            family=family,
            refresh_token=rotated_token,
            now=now,
        )

    async def authenticate_access_token(
        self, session: AsyncSession, *, access_token: str
    ) -> CurrentPrincipal:
        try:
            claims = self._access_tokens.decode(access_token)
        except AccessTokenError as exc:
            raise self._session_invalid_error() from exc
        now = datetime.now(UTC)
        user = await self._repository.get_user_by_id(session, claims.user_id)
        family = await self._repository.get_session_family_for_user(
            session,
            family_id=claims.session_family_id,
            user_id=claims.user_id,
        )
        valid = (
            user is not None
            and user.status == "active"
            and family is not None
            and family.revoked_at is None
            and family.expires_at > now
        )
        await session.rollback()
        if not valid:
            raise self._session_invalid_error()
        return CurrentPrincipal(
            user_id=claims.user_id,
            session_family_id=claims.session_family_id,
            token_id=claims.token_id,
        )

    async def logout(self, session: AsyncSession, *, principal: CurrentPrincipal) -> None:
        now = datetime.now(UTC)
        async with session.begin():
            family = await self._repository.get_session_family_for_user(
                session,
                family_id=principal.session_family_id,
                user_id=principal.user_id,
                for_update=True,
            )
            if family is not None and family.revoked_at is None:
                await self._repository.revoke_family(
                    session, family=family, revoked_at=now, reason="logout"
                )
                self._record_session_revocation(
                    session,
                    principal=principal,
                    family=family,
                    reason="logout",
                )
        await self._authorization_cache.invalidate([principal.user_id])

    async def logout_all(self, session: AsyncSession, *, principal: CurrentPrincipal) -> int:
        now = datetime.now(UTC)
        async with session.begin():
            count = await self._repository.revoke_user_sessions(
                session,
                user_id=principal.user_id,
                revoked_at=now,
                reason="logout_all",
            )
            self._audit.record(
                session,
                action="identity.sessions.revoked_all",
                outcome="success",
                resource_type="user",
                actor_user_id=principal.user_id,
                resource_id=principal.user_id,
                changes={"revoked_count": count},
                request_id=get_request_id(),
            )
            enqueue_event(
                session,
                event_type="identity.sessions.revoked_all",
                aggregate_type="user",
                aggregate_id=principal.user_id,
                payload={"reason": "logout_all"},
            )
        await self._authorization_cache.invalidate([principal.user_id])
        return count

    async def list_sessions(
        self, session: AsyncSession, *, principal: CurrentPrincipal
    ) -> list[SessionResponse]:
        families = await self._repository.list_session_families(session, user_id=principal.user_id)
        return [
            SessionResponse(
                id=family.id,
                device_name=family.device_name,
                device_platform=family.device_platform,
                created_at=family.created_at,
                last_used_at=family.last_used_at,
                expires_at=family.expires_at,
                revoked_at=family.revoked_at,
                current=family.id == principal.session_family_id,
            )
            for family in families
        ]

    async def revoke_session(
        self,
        session: AsyncSession,
        *,
        principal: CurrentPrincipal,
        family_id: UUID,
    ) -> None:
        now = datetime.now(UTC)
        async with session.begin():
            family = await self._repository.get_session_family_for_user(
                session,
                family_id=family_id,
                user_id=principal.user_id,
                for_update=True,
            )
            if family is None:
                raise AppError(
                    status=404,
                    code="SESSION_NOT_FOUND",
                    title="Session not found",
                )
            if family.revoked_at is None:
                await self._repository.revoke_family(
                    session, family=family, revoked_at=now, reason="user_revocation"
                )
                self._record_session_revocation(
                    session,
                    principal=principal,
                    family=family,
                    reason="user_revocation",
                )
        await self._authorization_cache.invalidate([principal.user_id])

    async def change_password(
        self,
        session: AsyncSession,
        *,
        principal: CurrentPrincipal,
        current_password: str,
        new_password: str,
    ) -> None:
        candidate = await self._repository.get_user_by_id(session, principal.user_id)
        candidate_hash = candidate.password_hash if candidate is not None else None
        normalized_email = candidate.normalized_email if candidate is not None else None
        await session.rollback()
        verified = await asyncio.to_thread(
            self._passwords.verify_or_dummy, candidate_hash, current_password
        )
        try:
            new_hash = await asyncio.to_thread(
                self._passwords.hash,
                new_password,
                normalized_email=normalized_email,
            )
        except ValueError as exc:
            raise AppError(
                status=422,
                code="PASSWORD_POLICY_FAILED",
                title="Password does not meet policy",
                detail=str(exc),
            ) from exc

        now = datetime.now(UTC)
        changed = False
        async with session.begin():
            user = await self._repository.get_user_by_id(
                session, principal.user_id, for_update=True
            )
            if (
                verified
                and user is not None
                and user.status == "active"
                and user.password_hash == candidate_hash
            ):
                user.password_hash = new_hash
                user.password_changed_at = now
                user.authorization_version += 1
                user.failed_login_attempts = 0
                user.login_locked_until = None
                await self._repository.revoke_user_sessions(
                    session,
                    user_id=user.id,
                    revoked_at=now,
                    reason="password_change",
                )
                self._audit.record(
                    session,
                    action="identity.password.changed",
                    outcome="success",
                    resource_type="user",
                    actor_user_id=user.id,
                    resource_id=user.id,
                    changes={"sessions_revoked": True},
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="identity.password.changed",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    payload={"reason": "password_change", "sessions_revoked": True},
                )
                changed = True
            elif user is not None:
                self._audit.record(
                    session,
                    action="identity.password.change_failed",
                    outcome="denied",
                    reason_code="AUTHENTICATION_FAILED",
                    resource_type="user",
                    actor_user_id=user.id,
                    resource_id=user.id,
                    request_id=get_request_id(),
                )
        if not changed:
            self._raise_authentication_failed()
        await self._authorization_cache.invalidate([principal.user_id])

    def _token_response(
        self,
        *,
        user_id: UUID,
        family: SessionFamily,
        refresh_token: str,
        now: datetime,
    ) -> TokenResponse:
        return TokenResponse(
            access_token=self._access_tokens.issue(
                user_id=user_id,
                session_family_id=family.id,
                now=now,
            ),
            refresh_token=refresh_token,
            expires_in=self._access_tokens.ttl_seconds,
            refresh_expires_at=family.expires_at,
            session_id=family.id,
        )

    def _record_session_revocation(
        self,
        session: AsyncSession,
        *,
        principal: CurrentPrincipal,
        family: SessionFamily,
        reason: str,
    ) -> None:
        self._audit.record(
            session,
            action="identity.session.revoked",
            outcome="success",
            resource_type="session_family",
            actor_user_id=principal.user_id,
            resource_id=family.id,
            changes={"reason": reason},
            request_id=get_request_id(),
        )
        enqueue_event(
            session,
            event_type="identity.session.revoked",
            aggregate_type="user",
            aggregate_id=principal.user_id,
            payload={"session_family_id": str(family.id), "reason": reason},
        )

    @staticmethod
    def _session_invalid_error() -> AppError:
        return AppError(
            status=401,
            code="SESSION_INVALID",
            title="Authentication is required",
        )

    @staticmethod
    def _raise_authentication_failed() -> Never:
        raise AppError(
            status=401,
            code="AUTHENTICATION_FAILED",
            title="Authentication failed",
            detail="The supplied credentials are invalid.",
        )
