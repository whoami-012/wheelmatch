from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.authorization.cache import AuthorizationCache
from app.modules.authorization.policy import (
    AuthorizationContext,
    AuthorizationPolicy,
    OrganizationAccess,
)
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.repository import IdentityRepository
from app.modules.profiles.repository import ProfileRepository
from app.modules.profiles.schemas import (
    CapabilitiesResponse,
    DealerCapabilityResponse,
)


class CapabilityService:
    def __init__(
        self,
        *,
        identity_repository: IdentityRepository,
        profile_repository: ProfileRepository,
        dealer_repository: DealerRepository,
        policy: AuthorizationPolicy,
        cache: AuthorizationCache,
    ) -> None:
        self._identities = identity_repository
        self._profiles = profile_repository
        self._dealers = dealer_repository
        self._policy = policy
        self._cache = cache

    async def get_capabilities(
        self, session: AsyncSession, *, actor_user_id: UUID
    ) -> CapabilitiesResponse:
        user = await self._identities.get_user_by_id(session, actor_user_id)
        if user is None or user.status != "active":
            raise AppError(
                status=403,
                code="ACCOUNT_INACTIVE",
                title="Account is not permitted to perform this action",
            )
        cached = await self._cache.get(user.id, expected_version=user.authorization_version)
        if cached is not None:
            return CapabilitiesResponse.model_validate(cached)

        seller = await self._profiles.get_seller_profile(session, user.id)
        base_context = AuthorizationContext(
            user_id=user.id,
            user_status=user.status,
            email_verified=user.email_verified_at is not None,
            phone_verified=user.phone_verified_at is not None,
            seller_profile_status=seller.status if seller else None,
            seller_readiness_state=seller.readiness_state if seller else None,
        )
        dealer_capabilities: list[DealerCapabilityResponse] = []
        for membership, organization in await self._dealers.list_user_memberships(session, user.id):
            context = AuthorizationContext(
                user_id=user.id,
                user_status=user.status,
                email_verified=base_context.email_verified,
                phone_verified=base_context.phone_verified,
                seller_profile_status=base_context.seller_profile_status,
                seller_readiness_state=base_context.seller_readiness_state,
                organization=OrganizationAccess(
                    organization_id=organization.id,
                    organization_status=organization.status,
                    organization_verification_status=organization.verification_status,
                    membership_id=membership.id,
                    membership_status=membership.status,
                    membership_role=membership.role,
                ),
            )
            permissions = sorted(
                permission.value for permission in self._policy.dealer_permissions(context)
            )
            if permissions:
                dealer_capabilities.append(
                    DealerCapabilityResponse(
                        organization_id=organization.id,
                        membership_id=membership.id,
                        role=membership.role,
                        permissions=permissions,
                    )
                )
        response = CapabilitiesResponse(
            buyer=self._policy.can_buy(base_context),
            personal_seller=self._policy.can_sell_personally(base_context),
            can_create_private_draft=self._policy.can_create_private_draft(base_context),
            dealer=dealer_capabilities,
        )
        await self._cache.set(
            user.id,
            version=user.authorization_version,
            projection=response.model_dump(mode="json"),
        )
        return response
