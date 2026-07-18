from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import IdempotencyConflictError, IdempotencyRepository
from app.core.ids import uuid7
from app.core.outbox.models import OutboxEvent
from app.core.outbox.service import enqueue_event
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.verification.models import IdentityVerification, UserVerificationState
from app.modules.verification.provider import IdentityVerificationProvider, ProviderResult
from app.modules.verification.repository import VerificationRepository
from app.modules.verification.schemas import (
    IdentityVerificationStartResponse,
    IdentityVerificationStatusResponse,
    ProviderResultApplyResponse,
)
from app.modules.verification.state import (
    ASSURANCE_LEVELS,
    PROVIDER_RESULT_STATUSES,
    TERMINAL_STATUSES,
    classify_provider_failure,
    require_transition,
    result_disposition,
)

EventWriter = Callable[..., OutboxEvent]
_SAFE_FAILURE_CODES = frozenset(
    {
        "IDENTITY_MISMATCH",
        "MANUAL_REVIEW_REQUIRED",
        "PROVIDER_REJECTED",
        "PROVIDER_UNAVAILABLE",
        "QUALITY_INSUFFICIENT",
        "VERIFICATION_FAILED",
    }
)


class IdentityVerificationService:
    def __init__(
        self,
        *,
        repository: VerificationRepository,
        identity_repository: IdentityRepository,
        provider: IdentityVerificationProvider,
        audit: AuditRecorder,
        idempotency_repository: IdempotencyRepository,
        event_writer: EventWriter = enqueue_event,
    ) -> None:
        self._repository = repository
        self._identities = identity_repository
        self._provider = provider
        self._audit = audit
        self._idempotency = idempotency_repository
        self._event_writer = event_writer

    async def start(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> IdentityVerificationStartResponse:
        attempt_id = await self._claim_attempt(
            session,
            actor_user_id=actor_user_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        attempt = await self._repository.get_attempt(session, attempt_id)
        if attempt is None or attempt.user_id != actor_user_id:
            raise self._not_found()
        attempt_status = attempt.status
        await session.rollback()
        if attempt_status == "manual_review":
            raise AppError(
                status=409,
                code="VERIFICATION_IN_REVIEW",
                title="Identity verification is under review",
            )
        if attempt_status in TERMINAL_STATUSES:
            raise AppError(
                status=409,
                code="VERIFICATION_ATTEMPT_CLOSED",
                title="Identity verification attempt is closed",
            )

        try:
            provider_session = await self._provider.create_session(
                attempt_id=attempt_id,
                user_id=actor_user_id,
                idempotency_reference=str(attempt_id),
            )
        except Exception as exc:
            classification = classify_provider_failure(exc)
            if not classification.retryable:
                await self._finalize_session_failure(
                    session,
                    attempt_id=attempt_id,
                    safe_failure_code=classification.safe_failure_code,
                )
            raise AppError(
                status=503,
                code="VERIFICATION_PROVIDER_UNAVAILABLE",
                title="Identity verification is temporarily unavailable",
            ) from exc

        await self._finalize_session(
            session,
            attempt_id=attempt_id,
            provider_reference=provider_session.provider_reference,
        )
        return IdentityVerificationStartResponse.model_validate(
            {
                "attempt_id": attempt_id,
                "status": "pending",
                "capture_url": provider_session.capture_url,
                "capture_expires_at": provider_session.capture_expires_at,
            }
        )

    async def status(
        self, session: AsyncSession, *, actor_user_id: UUID
    ) -> IdentityVerificationStatusResponse:
        state = await self._repository.get_state(session, actor_user_id)
        if state is None:
            raise self._not_found()
        return self._status_response(state)

    async def apply_provider_result(
        self, session: AsyncSession, result: ProviderResult
    ) -> ProviderResultApplyResponse:
        self._validate_result(result)
        async with session.begin():
            attempt = await self._repository.get_by_provider_reference(
                session,
                provider_identifier=self._provider.identifier,
                provider_reference=result.provider_reference,
                for_update=True,
            )
            if attempt is None:
                raise self._result_not_found()
            state = await self._repository.get_state(session, attempt.user_id, for_update=True)
            if state is None:
                raise self._result_not_found()

            existing_event = await self._repository.get_by_result_event(
                session,
                provider_identifier=self._provider.identifier,
                event_id=result.event_id,
            )
            if existing_event is not None:
                if existing_event.id == attempt.id and self._result_matches(attempt, result):
                    return self._apply_response(attempt, state.version, "duplicate")
                raise self._result_conflict()

            try:
                disposition = result_disposition(
                    attempt_status=attempt.status,
                    attempt_event_id=attempt.provider_result_event_id,
                    result_event_id=result.event_id,
                    result_matches=self._result_matches(attempt, result),
                    superseded=attempt.superseded_at is not None,
                    projection_current=state.current_attempt_id == attempt.id,
                )
            except ValueError as exc:
                raise self._result_conflict() from exc
            if disposition != "applied":
                return self._apply_response(attempt, state.version, disposition)

            try:
                require_transition(attempt.status, result.status)
            except ValueError as exc:
                raise self._result_conflict() from exc
            self._apply_result_to_attempt(attempt, result)
            self._apply_attempt_to_state(state, attempt)
            self._record_finalization(session, attempt=attempt, state=state)
            await session.flush()
            return self._apply_response(attempt, state.version, "applied")

    async def _claim_attempt(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> UUID:
        now = datetime.now(UTC)
        async with session.begin():
            try:
                reservation = await self._idempotency.reserve(
                    session,
                    scope=f"user:{actor_user_id}",
                    operation="identity_verification.start",
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
                return UUID(str(reservation.replay_body["attempt_id"]))

            user = await self._identities.get_user_by_id(session, actor_user_id, for_update=True)
            if user is None or user.status != "active":
                raise self._not_found()
            active = await self._repository.get_active_for_user(session, actor_user_id)
            if active is not None and active.status == "manual_review":
                raise AppError(
                    status=409,
                    code="VERIFICATION_IN_REVIEW",
                    title="Identity verification is under review",
                )
            attempt = active or await self._new_attempt(session, user=user, now=now)
            await self._idempotency.complete(
                session,
                scope=f"user:{actor_user_id}",
                operation="identity_verification.start",
                key=idempotency_key,
                response_status=201,
                response_body={"attempt_id": str(attempt.id)},
                resource_type="identity_verification",
                resource_id=attempt.id,
            )
            await session.flush()
            return attempt.id

    async def _new_attempt(
        self, session: AsyncSession, *, user: User, now: datetime
    ) -> IdentityVerification:
        latest = await self._repository.get_latest_for_user(session, user.id)
        if latest is not None:
            latest.superseded_at = now
        attempt = IdentityVerification(
            id=uuid7(),
            user_id=user.id,
            attempt_number=await self._repository.next_attempt_number(session, user.id),
            provider_identifier=self._provider.identifier,
            status="session_pending",
        )
        session.add(attempt)
        await session.flush()
        state = await self._repository.get_state(session, user.id, for_update=True)
        if state is None:
            session.add(
                UserVerificationState(
                    user_id=user.id,
                    current_attempt_id=attempt.id,
                    effective_status="session_pending",
                    version=1,
                )
            )
        else:
            state.current_attempt_id = attempt.id
            state.effective_status = "session_pending"
            state.assurance_level = None
            state.verified_at = None
            state.expires_at = None
            state.revoked_at = None
            state.safe_failure_code = None
            state.version += 1
        return attempt

    async def _finalize_session(
        self, session: AsyncSession, *, attempt_id: UUID, provider_reference: str
    ) -> None:
        async with session.begin():
            attempt = await self._repository.get_attempt(session, attempt_id, for_update=True)
            if attempt is None:
                raise self._not_found()
            state = await self._repository.get_state(session, attempt.user_id, for_update=True)
            if state is None or state.current_attempt_id != attempt.id:
                raise AppError(
                    status=409,
                    code="VERIFICATION_ATTEMPT_SUPERSEDED",
                    title="Identity verification attempt was superseded",
                )
            if attempt.provider_reference not in {None, provider_reference}:
                raise AppError(
                    status=503,
                    code="VERIFICATION_PROVIDER_UNAVAILABLE",
                    title="Identity verification is temporarily unavailable",
                )
            attempt.provider_reference = provider_reference
            if attempt.status == "session_pending":
                attempt.status = "pending"
                state.effective_status = "pending"
                state.version += 1
                self._record_finalization(session, attempt=attempt, state=state)
            elif attempt.status != "pending":
                raise AppError(
                    status=409,
                    code="VERIFICATION_ATTEMPT_CLOSED",
                    title="Identity verification attempt is closed",
                )
            await session.flush()

    async def _finalize_session_failure(
        self, session: AsyncSession, *, attempt_id: UUID, safe_failure_code: str
    ) -> None:
        async with session.begin():
            attempt = await self._repository.get_attempt(session, attempt_id, for_update=True)
            if attempt is None or attempt.status != "session_pending":
                return
            state = await self._repository.get_state(session, attempt.user_id, for_update=True)
            if state is None or state.current_attempt_id != attempt.id:
                return
            attempt.status = "failed"
            attempt.safe_failure_code = self._safe_failure_code(safe_failure_code)
            self._apply_attempt_to_state(state, attempt)
            self._record_finalization(session, attempt=attempt, state=state)
            await session.flush()

    def _record_finalization(
        self,
        session: AsyncSession,
        *,
        attempt: IdentityVerification,
        state: UserVerificationState,
    ) -> None:
        changes = {
            "status": attempt.status,
            "projection_version": state.version,
            "failure_code": attempt.safe_failure_code,
        }
        self._audit.record(
            session,
            action="identity.verification.state_changed",
            outcome="success",
            resource_type="identity_verification",
            actor_user_id=attempt.user_id,
            resource_id=attempt.id,
            changes=changes,
            request_id=get_request_id(),
        )
        self._event_writer(
            session,
            event_type="identity.verification.state_changed",
            aggregate_type="user",
            aggregate_id=attempt.user_id,
            payload={
                "user_id": str(attempt.user_id),
                "attempt_id": str(attempt.id),
                "projection_version": state.version,
                "state": state.effective_status,
                "expiry": state.expires_at.isoformat() if state.expires_at else None,
                "failure_code": state.safe_failure_code,
            },
        )

    @classmethod
    def _validate_result(cls, result: ProviderResult) -> None:
        if (
            not result.provider_reference
            or len(result.provider_reference) > 200
            or not result.event_id
            or len(result.event_id) > 200
            or result.status not in PROVIDER_RESULT_STATUSES
        ):
            raise cls._result_invalid()
        if result.status == "verified":
            if (
                result.assurance_level not in ASSURANCE_LEVELS
                or result.verified_at is None
                or result.expires_at is None
                or result.expires_at <= result.verified_at
                or result.safe_failure_code is not None
            ):
                raise cls._result_invalid()
        elif result.status == "failed" and not result.safe_failure_code:
            raise cls._result_invalid()

    @classmethod
    def _apply_result_to_attempt(
        cls, attempt: IdentityVerification, result: ProviderResult
    ) -> None:
        attempt.provider_result_event_id = result.event_id
        attempt.status = result.status
        attempt.assurance_level = result.assurance_level if result.status == "verified" else None
        attempt.verified_at = result.verified_at if result.status == "verified" else None
        attempt.expires_at = result.expires_at if result.status == "verified" else None
        attempt.revoked_at = None
        if result.status == "manual_review":
            attempt.safe_failure_code = "MANUAL_REVIEW_REQUIRED"
        elif result.status == "failed":
            attempt.safe_failure_code = cls._safe_failure_code(result.safe_failure_code)
        else:
            attempt.safe_failure_code = None

    @staticmethod
    def _apply_attempt_to_state(
        state: UserVerificationState, attempt: IdentityVerification
    ) -> None:
        state.current_attempt_id = attempt.id
        state.effective_status = attempt.status
        state.assurance_level = attempt.assurance_level
        state.verified_at = attempt.verified_at
        state.expires_at = attempt.expires_at
        state.revoked_at = attempt.revoked_at
        state.safe_failure_code = attempt.safe_failure_code
        state.version += 1

    @classmethod
    def _result_matches(cls, attempt: IdentityVerification, result: ProviderResult) -> bool:
        expected_failure = None
        if result.status == "manual_review":
            expected_failure = "MANUAL_REVIEW_REQUIRED"
        elif result.status == "failed":
            expected_failure = cls._safe_failure_code(result.safe_failure_code)
        return (
            attempt.provider_result_event_id == result.event_id
            and attempt.status == result.status
            and attempt.assurance_level
            == (result.assurance_level if result.status == "verified" else None)
            and attempt.verified_at == (result.verified_at if result.status == "verified" else None)
            and attempt.expires_at == (result.expires_at if result.status == "verified" else None)
            and attempt.safe_failure_code == expected_failure
        )

    @staticmethod
    def _safe_failure_code(value: str | None) -> str:
        return value if value in _SAFE_FAILURE_CODES else "VERIFICATION_FAILED"

    @staticmethod
    def _status_response(state: UserVerificationState) -> IdentityVerificationStatusResponse:
        return IdentityVerificationStatusResponse.model_validate(
            {
                "attempt_id": state.current_attempt_id,
                "status": state.effective_status,
                "assurance_level": state.assurance_level,
                "verified_at": state.verified_at,
                "expires_at": state.expires_at,
                "revoked_at": state.revoked_at,
                "version": state.version,
                "failure_code": state.safe_failure_code,
                "updated_at": state.updated_at,
            }
        )

    @staticmethod
    def _apply_response(
        attempt: IdentityVerification, version: int, disposition: str
    ) -> ProviderResultApplyResponse:
        return ProviderResultApplyResponse.model_validate(
            {
                "attempt_id": attempt.id,
                "status": attempt.status,
                "projection_version": version,
                "disposition": disposition,
            }
        )

    @staticmethod
    def _not_found() -> AppError:
        return AppError(
            status=404,
            code="VERIFICATION_NOT_FOUND",
            title="Identity verification was not found",
        )

    @staticmethod
    def _result_not_found() -> AppError:
        return AppError(
            status=404,
            code="VERIFICATION_RESULT_NOT_FOUND",
            title="Identity verification result target was not found",
        )

    @staticmethod
    def _result_invalid() -> AppError:
        return AppError(
            status=422,
            code="VERIFICATION_RESULT_INVALID",
            title="Identity verification result is invalid",
        )

    @staticmethod
    def _result_conflict() -> AppError:
        return AppError(
            status=409,
            code="VERIFICATION_RESULT_CONFLICT",
            title="Identity verification result conflicts with finalized state",
        )
