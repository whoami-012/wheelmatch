from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class DealerPermission(StrEnum):
    ORGANIZATION_SETTINGS = "organization.settings.manage"
    ORGANIZATION_SETTINGS_LIMITED = "organization.settings.manage_limited"
    MEMBERSHIP_MANAGE = "organization.memberships.manage"
    INVENTORY_MANAGE = "organization.inventory.manage"
    REQUEST_METADATA_VIEW = "organization.requests.metadata.view"
    INTEREST_MANAGE = "organization.interests.manage"
    CONVERSATION_ASSIGN = "organization.conversations.assign"
    CONVERSATION_WORK_ASSIGNED = "organization.conversations.work_assigned"


ROLE_PERMISSIONS: dict[str, frozenset[DealerPermission]] = {
    "owner": frozenset(DealerPermission),
    "admin": frozenset(
        {
            DealerPermission.ORGANIZATION_SETTINGS_LIMITED,
            DealerPermission.MEMBERSHIP_MANAGE,
            DealerPermission.INVENTORY_MANAGE,
            DealerPermission.REQUEST_METADATA_VIEW,
            DealerPermission.INTEREST_MANAGE,
            DealerPermission.CONVERSATION_ASSIGN,
        }
    ),
    "inventory_manager": frozenset({DealerPermission.INVENTORY_MANAGE}),
    "sales_agent": frozenset(
        {
            DealerPermission.INTEREST_MANAGE,
            DealerPermission.CONVERSATION_WORK_ASSIGNED,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class OrganizationAccess:
    organization_id: UUID
    organization_status: str
    organization_verification_status: str
    membership_id: UUID
    membership_status: str
    membership_role: str


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    user_id: UUID
    user_status: str
    email_verified: bool
    phone_verified: bool
    seller_profile_status: str | None = None
    seller_readiness_state: str | None = None
    organization: OrganizationAccess | None = None


class AuthorizationPolicy:
    """Pure, deny-by-default policy. PostgreSQL-backed callers build the context."""

    @staticmethod
    def can_buy(context: AuthorizationContext) -> bool:
        return context.user_status == "active"

    @staticmethod
    def can_create_private_draft(context: AuthorizationContext) -> bool:
        return context.user_status == "active" and context.email_verified and context.phone_verified

    @staticmethod
    def can_sell_personally(context: AuthorizationContext) -> bool:
        return (
            context.user_status == "active"
            and context.seller_profile_status == "active"
            and context.seller_readiness_state == "ready"
        )

    @staticmethod
    def dealer_permissions(context: AuthorizationContext) -> frozenset[DealerPermission]:
        organization = context.organization
        if (
            context.user_status != "active"
            or organization is None
            or organization.organization_status != "active"
            or organization.organization_verification_status != "verified"
            or organization.membership_status != "active"
        ):
            return frozenset()
        return ROLE_PERMISSIONS.get(organization.membership_role, frozenset())

    def permits(self, context: AuthorizationContext, permission: DealerPermission) -> bool:
        return permission in self.dealer_permissions(context)

    def can_manage_owned_resource(
        self,
        context: AuthorizationContext,
        *,
        owner_user_id: UUID | None,
        owner_organization_id: UUID | None,
        dealer_permission: DealerPermission,
    ) -> bool:
        if context.user_status != "active":
            return False
        if owner_user_id is not None:
            return owner_user_id == context.user_id and owner_organization_id is None
        organization = context.organization
        return (
            owner_organization_id is not None
            and organization is not None
            and owner_organization_id == organization.organization_id
            and self.permits(context, dealer_permission)
        )
