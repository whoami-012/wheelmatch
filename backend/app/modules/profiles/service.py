from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.outbox import enqueue_event
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.authorization.cache import AuthorizationCache
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.profiles.models import SellerProfile
from app.modules.profiles.repository import ProfileRepository
from app.modules.profiles.schemas import (
    ProfileResponse,
    ProfileUpdateRequest,
    SellerProfileResponse,
)


class ProfileService:
    def __init__(
        self,
        *,
        repository: ProfileRepository,
        identity_repository: IdentityRepository,
        audit: AuditRecorder,
        authorization_cache: AuthorizationCache,
    ) -> None:
        self._repository = repository
        self._identities = identity_repository
        self._audit = audit
        self._authorization_cache = authorization_cache

    async def get_profile(self, session: AsyncSession, *, actor_user_id: UUID) -> ProfileResponse:
        bundle = await self._repository.get_user_and_profile(session, actor_user_id)
        if bundle is None or bundle[0].status != "active":
            raise AppError(
                status=404,
                code="PROFILE_NOT_FOUND",
                title="Profile not found",
            )
        user, profile = bundle
        return ProfileResponse(
            user_id=user.id,
            email=user.normalized_email,
            phone=user.normalized_phone,
            email_verified=user.email_verified_at is not None,
            phone_verified=user.phone_verified_at is not None,
            display_name=profile.display_name,
            home_locality=profile.home_locality,
            version=profile.version,
            updated_at=profile.updated_at,
        )

    async def update_profile(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        request: ProfileUpdateRequest,
    ) -> ProfileResponse:
        async with session.begin():
            user = await self._identities.get_user_by_id(session, actor_user_id, for_update=True)
            profile = await self._repository.get_profile(session, actor_user_id, for_update=True)
            if user is None or profile is None or user.status != "active":
                raise AppError(status=404, code="PROFILE_NOT_FOUND", title="Profile not found")
            if profile.version != request.expected_version:
                raise AppError(
                    status=409,
                    code="PROFILE_VERSION_CONFLICT",
                    title="Profile state changed",
                )
            changed_fields: list[str] = []
            if (
                "display_name" in request.model_fields_set
                and profile.display_name != request.display_name
            ):
                profile.display_name = request.display_name
                changed_fields.append("display_name")
            if (
                "home_locality" in request.model_fields_set
                and profile.home_locality != request.home_locality
            ):
                profile.home_locality = request.home_locality
                changed_fields.append("home_locality")
            if changed_fields:
                profile.version += 1
                self._audit.record(
                    session,
                    action="profile.updated",
                    outcome="success",
                    resource_type="profile",
                    actor_user_id=user.id,
                    resource_id=user.id,
                    changes={
                        "changed_fields": ",".join(changed_fields),
                        "version": profile.version,
                    },
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="profile.updated",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    payload={"changed_fields": changed_fields, "version": profile.version},
                )
            await session.flush()
            if changed_fields:
                await session.refresh(profile, attribute_names=["updated_at"])
            response = ProfileResponse(
                user_id=user.id,
                email=user.normalized_email,
                phone=user.normalized_phone,
                email_verified=user.email_verified_at is not None,
                phone_verified=user.phone_verified_at is not None,
                display_name=profile.display_name,
                home_locality=profile.home_locality,
                version=profile.version,
                updated_at=profile.updated_at,
            )
        return response

    async def create_seller_profile(
        self, session: AsyncSession, *, actor_user_id: UUID
    ) -> SellerProfileResponse:
        async with session.begin():
            user = await self._identities.get_user_by_id(session, actor_user_id, for_update=True)
            if user is None or user.status != "active":
                raise AppError(status=404, code="PROFILE_NOT_FOUND", title="Profile not found")
            seller = await self._repository.get_seller_profile(
                session, actor_user_id, for_update=True
            )
            if seller is None:
                seller = SellerProfile(
                    user_id=user.id,
                    status="pending",
                    readiness_state="not_ready",
                )
                session.add(seller)
                user.authorization_version += 1
                self._audit.record(
                    session,
                    action="profile.seller.created",
                    outcome="success",
                    resource_type="seller_profile",
                    actor_user_id=user.id,
                    resource_id=user.id,
                    changes={"status": "pending", "readiness_state": "not_ready"},
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="profile.seller.created",
                    aggregate_type="user",
                    aggregate_id=user.id,
                    payload={"authorization_version": user.authorization_version},
                )
            await session.flush()
        await self._authorization_cache.invalidate([actor_user_id])
        return self._seller_response(user, seller)

    async def get_seller_readiness(
        self, session: AsyncSession, *, actor_user_id: UUID
    ) -> SellerProfileResponse:
        user = await self._identities.get_user_by_id(session, actor_user_id)
        if user is None or user.status != "active":
            raise AppError(status=404, code="PROFILE_NOT_FOUND", title="Profile not found")
        seller = await self._repository.get_seller_profile(session, actor_user_id)
        if seller is None:
            seller = SellerProfile(
                user_id=user.id,
                status="pending",
                readiness_state="not_ready",
            )
        return self._seller_response(user, seller)

    @staticmethod
    def _seller_response(user: User, seller: SellerProfile) -> SellerProfileResponse:
        missing = ["identity_verification", "publication_policy"]
        if user.email_verified_at is None:
            missing.insert(0, "email_verification")
        if user.phone_verified_at is None:
            missing.insert(0, "phone_verification")
        return SellerProfileResponse(
            user_id=seller.user_id,
            status=seller.status,
            readiness_state=seller.readiness_state,
            activated_at=seller.activated_at,
            missing_requirements=missing,
        )
