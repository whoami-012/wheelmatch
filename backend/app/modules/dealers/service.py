from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import IdempotencyConflictError, IdempotencyRepository
from app.core.ids import uuid7
from app.core.outbox import enqueue_event
from app.core.security import SecretHasher, generate_opaque_token
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.authorization.cache import AuthorizationCache
from app.modules.authorization.policy import (
    AuthorizationContext,
    AuthorizationPolicy,
    DealerPermission,
    OrganizationAccess,
)
from app.modules.dealers.delivery import DealerInvitationDelivery
from app.modules.dealers.models import DealerMembership, DealerOrganization
from app.modules.dealers.repository import DealerRepository
from app.modules.dealers.schemas import (
    MembershipListItem,
    MembershipResponse,
    MembershipUpdateRequest,
    OrganizationCreateRequest,
    OrganizationResponse,
)
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository


class DealerService:
    def __init__(
        self,
        *,
        repository: DealerRepository,
        identity_repository: IdentityRepository,
        policy: AuthorizationPolicy,
        secret_hasher: SecretHasher,
        audit: AuditRecorder,
        delivery: DealerInvitationDelivery,
        authorization_cache: AuthorizationCache,
        idempotency_repository: IdempotencyRepository,
        invitation_ttl_seconds: int,
    ) -> None:
        self._repository = repository
        self._identities = identity_repository
        self._policy = policy
        self._secrets = secret_hasher
        self._audit = audit
        self._delivery = delivery
        self._authorization_cache = authorization_cache
        self._idempotency = idempotency_repository
        self._invitation_ttl = timedelta(seconds=invitation_ttl_seconds)

    async def create_organization(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        request: OrganizationCreateRequest,
        idempotency_key: str,
        request_hash: str,
    ) -> OrganizationResponse:
        now = datetime.now(UTC)
        organization = DealerOrganization(
            id=uuid7(),
            legal_name=request.legal_name,
            display_name=request.display_name,
            status="active",
            verification_status="pending",
            created_by_user_id=actor_user_id,
        )
        owner_membership = DealerMembership(
            id=uuid7(),
            organization_id=organization.id,
            user_id=actor_user_id,
            role="owner",
            status="active",
            invited_by_user_id=actor_user_id,
            accepted_at=now,
        )
        response: OrganizationResponse | None = None
        async with session.begin():
            try:
                reservation = await self._idempotency.reserve(
                    session,
                    scope=f"user:{actor_user_id}",
                    operation="dealer.organization.create",
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
                return OrganizationResponse.model_validate(reservation.replay_body)
            actor = await self._identities.get_user_by_id(session, actor_user_id, for_update=True)
            if actor is None or actor.status != "active":
                raise AppError(
                    status=403,
                    code="ACCOUNT_INACTIVE",
                    title="Account is not permitted to perform this action",
                )
            session.add(organization)
            await session.flush()
            session.add(owner_membership)
            await session.flush()
            actor.authorization_version += 1
            self._audit.record(
                session,
                action="dealer.organization.created",
                outcome="success",
                resource_type="dealer_organization",
                actor_user_id=actor.id,
                resource_id=organization.id,
                organization_id=organization.id,
                membership_id=owner_membership.id,
                changes={"status": "active", "verification_status": "pending"},
                request_id=get_request_id(),
            )
            enqueue_event(
                session,
                event_type="dealer.organization.created",
                aggregate_type="dealer_organization",
                aggregate_id=organization.id,
                payload={
                    "owner_membership_id": str(owner_membership.id),
                    "verification_status": "pending",
                },
            )
            await session.flush()
            response = self._organization_response(organization)
            await self._idempotency.complete(
                session,
                scope=f"user:{actor_user_id}",
                operation="dealer.organization.create",
                key=idempotency_key,
                response_status=201,
                response_body=response.model_dump(mode="json"),
                resource_type="dealer_organization",
                resource_id=organization.id,
            )
        await self._authorization_cache.invalidate([actor_user_id])
        if response is None:
            raise AssertionError("organization creation completed without a response")
        return response

    async def get_organization(
        self, session: AsyncSession, *, actor_user_id: UUID, organization_id: UUID
    ) -> OrganizationResponse:
        actor = await self._identities.get_user_by_id(session, actor_user_id)
        organization = await self._repository.get_organization(session, organization_id)
        membership = await self._repository.get_membership_for_user(
            session, organization_id=organization_id, user_id=actor_user_id
        )
        if (
            actor is None
            or actor.status != "active"
            or organization is None
            or membership is None
            or membership.status != "active"
        ):
            raise AppError(
                status=404,
                code="ORGANIZATION_NOT_FOUND",
                title="Organization not found",
            )
        return self._organization_response(organization)

    async def list_memberships(
        self, session: AsyncSession, *, actor_user_id: UUID
    ) -> list[MembershipListItem]:
        actor = await self._identities.get_user_by_id(session, actor_user_id)
        if actor is None or actor.status != "active":
            raise AppError(status=404, code="ACCOUNT_NOT_FOUND", title="Account not found")
        rows = await self._repository.list_user_memberships(session, actor_user_id)
        return [
            MembershipListItem(
                **self._membership_response(membership).model_dump(),
                organization_display_name=organization.display_name,
                organization_status=organization.status,
                organization_verification_status=organization.verification_status,
            )
            for membership, organization in rows
        ]

    async def invite_member(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        organization_id: UUID,
        target_user_id: UUID,
        role: str,
        idempotency_key: str,
        request_hash: str,
    ) -> MembershipResponse:
        now = datetime.now(UTC)
        token = generate_opaque_token()
        membership = DealerMembership(
            id=uuid7(),
            organization_id=organization_id,
            user_id=target_user_id,
            role=role,
            status="invited",
            invited_by_user_id=actor_user_id,
            invitation_token_hash=self._secrets.digest(token),
            invite_expires_at=now + self._invitation_ttl,
        )
        response: MembershipResponse | None = None
        try:
            async with session.begin():
                try:
                    reservation = await self._idempotency.reserve(
                        session,
                        scope=f"user:{actor_user_id}",
                        operation=f"dealer.organization.{organization_id}.membership.invite",
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
                    return MembershipResponse.model_validate(reservation.replay_body)
                actor, organization, actor_membership = await self._load_authorization_context(
                    session,
                    actor_user_id=actor_user_id,
                    organization_id=organization_id,
                    for_update=True,
                )
                self._require_membership_permission(
                    actor, organization, actor_membership, DealerPermission.MEMBERSHIP_MANAGE
                )
                if role == "owner" and actor_membership.role != "owner":
                    self._deny_membership_action()
                target = await self._identities.get_user_by_id(
                    session, target_user_id, for_update=True
                )
                if target is None or target.status != "active":
                    raise AppError(
                        status=404,
                        code="MEMBERSHIP_TARGET_NOT_FOUND",
                        title="Membership target not found",
                    )
                existing = await self._repository.get_membership_for_user(
                    session,
                    organization_id=organization_id,
                    user_id=target_user_id,
                    for_update=True,
                )
                if existing is not None:
                    raise AppError(
                        status=409,
                        code="MEMBERSHIP_ALREADY_EXISTS",
                        title="Membership already exists",
                    )
                session.add(membership)
                target.authorization_version += 1
                organization.authorization_version += 1
                self._audit.record(
                    session,
                    action="dealer.membership.invited",
                    outcome="success",
                    resource_type="dealer_membership",
                    actor_user_id=actor.id,
                    resource_id=membership.id,
                    organization_id=organization.id,
                    membership_id=actor_membership.id,
                    changes={"role": role, "status": "invited"},
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="dealer.membership.invited",
                    aggregate_type="dealer_organization",
                    aggregate_id=organization.id,
                    payload={
                        "membership_id": str(membership.id),
                        "role": role,
                        "authorization_version": organization.authorization_version,
                    },
                )
                await session.flush()
                response = self._membership_response(membership)
                await self._idempotency.complete(
                    session,
                    scope=f"user:{actor_user_id}",
                    operation=f"dealer.organization.{organization_id}.membership.invite",
                    key=idempotency_key,
                    response_status=201,
                    response_body=response.model_dump(mode="json"),
                    resource_type="dealer_membership",
                    resource_id=membership.id,
                )
        except IntegrityError as exc:
            raise AppError(
                status=409,
                code="MEMBERSHIP_ALREADY_EXISTS",
                title="Membership already exists",
            ) from exc
        await self._authorization_cache.invalidate([target_user_id, actor_user_id])
        await self._delivery.send_invitation(
            user_id=target_user_id,
            organization_id=organization_id,
            membership_id=membership.id,
            token=token,
        )
        if response is None:
            raise AssertionError("membership invitation completed without a response")
        return response

    async def accept_invitation(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        membership_id: UUID,
        invitation_token: str,
    ) -> MembershipResponse:
        now = datetime.now(UTC)
        accepted: DealerMembership | None = None
        async with session.begin():
            actor = await self._identities.get_user_by_id(session, actor_user_id, for_update=True)
            membership = await self._repository.get_membership(
                session, membership_id, for_update=True
            )
            if (
                actor is None
                or actor.status != "active"
                or membership is None
                or membership.user_id != actor_user_id
                or membership.status != "invited"
                or membership.invite_expires_at is None
                or membership.invite_expires_at <= now
                or membership.invitation_token_hash is None
                or not self._secrets.verify(invitation_token, membership.invitation_token_hash)
            ):
                self._secrets.verify(invitation_token, "0" * 64)
            else:
                organization = await self._repository.get_organization(
                    session, membership.organization_id, for_update=True
                )
                if organization is not None and organization.status == "active":
                    membership.status = "active"
                    membership.accepted_at = now
                    membership.invitation_token_hash = None
                    membership.version += 1
                    actor.authorization_version += 1
                    organization.authorization_version += 1
                    accepted = membership
                    self._audit.record(
                        session,
                        action="dealer.membership.accepted",
                        outcome="success",
                        resource_type="dealer_membership",
                        actor_user_id=actor.id,
                        resource_id=membership.id,
                        organization_id=organization.id,
                        membership_id=membership.id,
                        changes={"status": "active", "role": membership.role},
                        request_id=get_request_id(),
                    )
                    enqueue_event(
                        session,
                        event_type="dealer.membership.activated",
                        aggregate_type="dealer_organization",
                        aggregate_id=organization.id,
                        payload={
                            "membership_id": str(membership.id),
                            "authorization_version": organization.authorization_version,
                        },
                    )
        if accepted is None:
            raise AppError(
                status=400,
                code="MEMBERSHIP_INVITATION_INVALID",
                title="Membership invitation is invalid",
            )
        await self._authorization_cache.invalidate([actor_user_id])
        return self._membership_response(accepted)

    async def update_membership(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        organization_id: UUID,
        membership_id: UUID,
        request: MembershipUpdateRequest,
    ) -> MembershipResponse:
        if request.role is None and request.status is None:
            raise AppError(
                status=422,
                code="MEMBERSHIP_UPDATE_EMPTY",
                title="Membership update is empty",
            )
        now = datetime.now(UTC)
        target_user_id: UUID | None = None
        updated: DealerMembership | None = None
        async with session.begin():
            actor, organization, actor_membership = await self._load_authorization_context(
                session,
                actor_user_id=actor_user_id,
                organization_id=organization_id,
                for_update=True,
            )
            self._require_membership_permission(
                actor, organization, actor_membership, DealerPermission.MEMBERSHIP_MANAGE
            )
            membership = await self._repository.get_membership(
                session, membership_id, for_update=True
            )
            if membership is None or membership.organization_id != organization_id:
                raise AppError(
                    status=404,
                    code="MEMBERSHIP_NOT_FOUND",
                    title="Membership not found",
                )
            if membership.version != request.expected_version:
                raise AppError(
                    status=409,
                    code="MEMBERSHIP_VERSION_CONFLICT",
                    title="Membership state changed",
                )
            if (membership.role == "owner" or request.role == "owner") and (
                actor_membership.role != "owner"
            ):
                self._deny_membership_action()
            removes_active_owner = (
                membership.role == "owner"
                and membership.status == "active"
                and (
                    (request.role is not None and request.role != "owner")
                    or request.status in {"suspended", "revoked"}
                )
            )
            if removes_active_owner and not await self._has_other_owner(session, membership):
                raise AppError(
                    status=409,
                    code="LAST_ACTIVE_OWNER_REQUIRED",
                    title="Organization requires an active owner",
                )

            previous_role = membership.role
            previous_status = membership.status
            if request.role is not None:
                membership.role = request.role
            if request.status is not None:
                self._apply_status_transition(membership, request.status, now)
            if membership.role == previous_role and membership.status == previous_status:
                updated = membership
            else:
                membership.version += 1
                organization.authorization_version += 1
                target = await self._identities.get_user_by_id(
                    session, membership.user_id, for_update=True
                )
                if target is not None:
                    target.authorization_version += 1
                target_user_id = membership.user_id
                updated = membership
                self._audit.record(
                    session,
                    action="dealer.membership.updated",
                    outcome="success",
                    resource_type="dealer_membership",
                    actor_user_id=actor.id,
                    resource_id=membership.id,
                    organization_id=organization.id,
                    membership_id=actor_membership.id,
                    changes={
                        "previous_role": previous_role,
                        "role": membership.role,
                        "previous_status": previous_status,
                        "status": membership.status,
                        "version": membership.version,
                    },
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="dealer.membership.authorization_changed",
                    aggregate_type="dealer_organization",
                    aggregate_id=organization.id,
                    payload={
                        "membership_id": str(membership.id),
                        "role": membership.role,
                        "status": membership.status,
                        "authorization_version": organization.authorization_version,
                    },
                )
            await session.flush()
        if updated is None:
            raise AssertionError("membership update completed without a result")
        await self._authorization_cache.invalidate(
            [user_id for user_id in (target_user_id, actor_user_id) if user_id is not None]
        )
        return self._membership_response(updated)

    async def leave_organization(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        membership_id: UUID,
        expected_version: int,
    ) -> MembershipResponse:
        now = datetime.now(UTC)
        left: DealerMembership | None = None
        async with session.begin():
            actor = await self._identities.get_user_by_id(session, actor_user_id, for_update=True)
            membership = await self._repository.get_membership(
                session, membership_id, for_update=True
            )
            if (
                actor is None
                or actor.status != "active"
                or membership is None
                or membership.user_id != actor_user_id
                or membership.status != "active"
            ):
                raise AppError(
                    status=404,
                    code="MEMBERSHIP_NOT_FOUND",
                    title="Membership not found",
                )
            if membership.version != expected_version:
                raise AppError(
                    status=409,
                    code="MEMBERSHIP_VERSION_CONFLICT",
                    title="Membership state changed",
                )
            organization = await self._repository.get_organization(
                session, membership.organization_id, for_update=True
            )
            if organization is None:
                raise AppError(
                    status=404,
                    code="MEMBERSHIP_NOT_FOUND",
                    title="Membership not found",
                )
            if membership.role == "owner" and not await self._has_other_owner(session, membership):
                raise AppError(
                    status=409,
                    code="OWNER_TRANSFER_REQUIRED",
                    title="Ownership transfer is required before leaving",
                )
            membership.status = "left"
            membership.left_at = now
            membership.version += 1
            actor.authorization_version += 1
            organization.authorization_version += 1
            left = membership
            self._audit.record(
                session,
                action="dealer.membership.left",
                outcome="success",
                resource_type="dealer_membership",
                actor_user_id=actor.id,
                resource_id=membership.id,
                organization_id=organization.id,
                membership_id=membership.id,
                changes={"status": "left", "version": membership.version},
                request_id=get_request_id(),
            )
            enqueue_event(
                session,
                event_type="dealer.membership.authorization_changed",
                aggregate_type="dealer_organization",
                aggregate_id=organization.id,
                payload={
                    "membership_id": str(membership.id),
                    "status": "left",
                    "authorization_version": organization.authorization_version,
                },
            )
            await session.flush()
        await self._authorization_cache.invalidate([actor_user_id])
        return self._membership_response(left)

    async def _load_authorization_context(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        organization_id: UUID,
        for_update: bool,
    ) -> tuple[User, DealerOrganization, DealerMembership]:
        actor = await self._identities.get_user_by_id(session, actor_user_id, for_update=for_update)
        organization = await self._repository.get_organization(
            session, organization_id, for_update=for_update
        )
        membership = await self._repository.get_membership_for_user(
            session,
            organization_id=organization_id,
            user_id=actor_user_id,
            for_update=for_update,
        )
        if actor is None or organization is None or membership is None:
            raise AppError(
                status=404,
                code="ORGANIZATION_NOT_FOUND",
                title="Organization not found",
            )
        return actor, organization, membership

    def _require_membership_permission(
        self,
        actor: User,
        organization: DealerOrganization,
        membership: DealerMembership,
        permission: DealerPermission,
    ) -> None:
        context = AuthorizationContext(
            user_id=actor.id,
            user_status=actor.status,
            email_verified=actor.email_verified_at is not None,
            phone_verified=actor.phone_verified_at is not None,
            organization=OrganizationAccess(
                organization_id=organization.id,
                organization_status=organization.status,
                organization_verification_status=organization.verification_status,
                membership_id=membership.id,
                membership_status=membership.status,
                membership_role=membership.role,
            ),
        )
        if not self._policy.permits(context, permission):
            self._deny_membership_action()

    @staticmethod
    def _deny_membership_action() -> None:
        raise AppError(
            status=403,
            code="DEALER_PERMISSION_DENIED",
            title="Dealer permission denied",
        )

    async def _has_other_owner(self, session: AsyncSession, membership: DealerMembership) -> bool:
        return (
            await self._repository.count_other_active_owners(
                session,
                organization_id=membership.organization_id,
                excluded_membership_id=membership.id,
            )
            > 0
        )

    @staticmethod
    def _apply_status_transition(
        membership: DealerMembership, target_status: str, now: datetime
    ) -> None:
        allowed = {
            "active": {"suspended", "revoked"},
            "suspended": {"active", "revoked"},
            "invited": {"revoked"},
        }
        if target_status == membership.status:
            return
        if target_status not in allowed.get(membership.status, set()):
            raise AppError(
                status=409,
                code="MEMBERSHIP_TRANSITION_INVALID",
                title="Membership transition is invalid",
            )
        if target_status == "active":
            membership.suspended_at = None
        elif target_status == "suspended":
            membership.suspended_at = now
        elif target_status == "revoked":
            membership.revoked_at = now
        membership.status = target_status

    @staticmethod
    def _organization_response(organization: DealerOrganization) -> OrganizationResponse:
        return OrganizationResponse(
            id=organization.id,
            legal_name=organization.legal_name,
            display_name=organization.display_name,
            status=organization.status,
            verification_status=organization.verification_status,
            authorization_version=organization.authorization_version,
            created_at=organization.created_at,
        )

    @staticmethod
    def _membership_response(membership: DealerMembership) -> MembershipResponse:
        return MembershipResponse(
            id=membership.id,
            organization_id=membership.organization_id,
            user_id=membership.user_id,
            role=membership.role,
            status=membership.status,
            version=membership.version,
            invite_expires_at=membership.invite_expires_at,
            accepted_at=membership.accepted_at,
            suspended_at=membership.suspended_at,
            left_at=membership.left_at,
            revoked_at=membership.revoked_at,
            created_at=membership.created_at,
        )
