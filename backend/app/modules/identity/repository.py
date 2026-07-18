from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.models import (
    PasswordRecoveryChallenge,
    RefreshSession,
    SessionFamily,
    User,
    VerificationChallenge,
)


class IdentityRepository:
    async def get_user_by_email(
        self, session: AsyncSession, normalized_email: str, *, for_update: bool = False
    ) -> User | None:
        statement: Select[tuple[User]] = select(User).where(
            User.normalized_email == normalized_email, User.deleted_at.is_(None)
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(User | None, await session.scalar(statement))

    async def get_user_by_id(
        self, session: AsyncSession, user_id: UUID, *, for_update: bool = False
    ) -> User | None:
        statement: Select[tuple[User]] = select(User).where(
            User.id == user_id, User.deleted_at.is_(None)
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(User | None, await session.scalar(statement))

    async def get_verification_challenge(
        self, session: AsyncSession, challenge_id: UUID, *, for_update: bool = False
    ) -> VerificationChallenge | None:
        statement: Select[tuple[VerificationChallenge]] = select(VerificationChallenge).where(
            VerificationChallenge.id == challenge_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(VerificationChallenge | None, await session.scalar(statement))

    async def consume_open_verification_challenges(
        self, session: AsyncSession, *, user_id: UUID, kind: str, consumed_at: datetime
    ) -> None:
        await session.execute(
            update(VerificationChallenge)
            .where(
                VerificationChallenge.user_id == user_id,
                VerificationChallenge.kind == kind,
                VerificationChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=consumed_at)
        )

    async def get_recovery_by_hash(
        self, session: AsyncSession, token_hash: str, *, for_update: bool = False
    ) -> PasswordRecoveryChallenge | None:
        statement: Select[tuple[PasswordRecoveryChallenge]] = select(
            PasswordRecoveryChallenge
        ).where(PasswordRecoveryChallenge.token_hash == token_hash)
        if for_update:
            statement = statement.with_for_update()
        return cast(PasswordRecoveryChallenge | None, await session.scalar(statement))

    async def consume_open_recovery_challenges(
        self, session: AsyncSession, *, user_id: UUID, consumed_at: datetime
    ) -> None:
        await session.execute(
            update(PasswordRecoveryChallenge)
            .where(
                PasswordRecoveryChallenge.user_id == user_id,
                PasswordRecoveryChallenge.consumed_at.is_(None),
            )
            .values(consumed_at=consumed_at)
        )

    async def revoke_user_sessions(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        revoked_at: datetime,
        reason: str,
        exclude_family_id: UUID | None = None,
    ) -> int:
        family_ids = select(SessionFamily.id).where(
            SessionFamily.user_id == user_id, SessionFamily.revoked_at.is_(None)
        )
        if exclude_family_id is not None:
            family_ids = family_ids.where(SessionFamily.id != exclude_family_id)
        await session.execute(
            update(RefreshSession)
            .where(RefreshSession.family_id.in_(family_ids), RefreshSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        result = await session.execute(
            update(SessionFamily)
            .where(SessionFamily.id.in_(family_ids))
            .values(revoked_at=revoked_at, revocation_reason=reason)
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def get_refresh_session_by_hash(
        self, session: AsyncSession, token_hash: str, *, for_update: bool = False
    ) -> tuple[RefreshSession, SessionFamily, User] | None:
        statement = (
            select(RefreshSession, SessionFamily, User)
            .join(SessionFamily, SessionFamily.id == RefreshSession.family_id)
            .join(User, User.id == SessionFamily.user_id)
            .where(RefreshSession.token_hash == token_hash)
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await session.execute(statement)).one_or_none()
        return (row[0], row[1], row[2]) if row is not None else None

    async def get_session_family_for_user(
        self,
        session: AsyncSession,
        *,
        family_id: UUID,
        user_id: UUID,
        for_update: bool = False,
    ) -> SessionFamily | None:
        statement: Select[tuple[SessionFamily]] = select(SessionFamily).where(
            SessionFamily.id == family_id, SessionFamily.user_id == user_id
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(SessionFamily | None, await session.scalar(statement))

    async def list_session_families(
        self, session: AsyncSession, *, user_id: UUID
    ) -> list[SessionFamily]:
        result = await session.scalars(
            select(SessionFamily)
            .where(SessionFamily.user_id == user_id)
            .order_by(SessionFamily.last_used_at.desc(), SessionFamily.id.desc())
            .limit(100)
        )
        return list(result)

    async def revoke_family(
        self,
        session: AsyncSession,
        *,
        family: SessionFamily,
        revoked_at: datetime,
        reason: str,
        reuse_detected: bool = False,
    ) -> None:
        await session.execute(
            update(RefreshSession)
            .where(RefreshSession.family_id == family.id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=revoked_at)
        )
        family.revoked_at = family.revoked_at or revoked_at
        family.revocation_reason = family.revocation_reason or reason
        if reuse_detected:
            family.reuse_detected_at = family.reuse_detected_at or revoked_at
