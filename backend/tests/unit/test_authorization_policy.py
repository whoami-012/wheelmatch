from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.authorization.policy import (
    ROLE_PERMISSIONS,
    AuthorizationContext,
    AuthorizationPolicy,
    DealerPermission,
    OrganizationAccess,
)

USER_ID = UUID("018f0000-0000-7000-8000-000000000001")
ORG_ID = UUID("018f0000-0000-7000-8000-000000000002")
MEMBERSHIP_ID = UUID("018f0000-0000-7000-8000-000000000003")


def context(
    *,
    user_status: str = "active",
    role: str = "owner",
    membership_status: str = "active",
    organization_status: str = "active",
    organization_verification_status: str = "verified",
) -> AuthorizationContext:
    return AuthorizationContext(
        user_id=USER_ID,
        user_status=user_status,
        email_verified=True,
        phone_verified=True,
        seller_profile_status="active",
        seller_readiness_state="ready",
        organization=OrganizationAccess(
            organization_id=ORG_ID,
            organization_status=organization_status,
            organization_verification_status=organization_verification_status,
            membership_id=MEMBERSHIP_ID,
            membership_status=membership_status,
            membership_role=role,
        ),
    )


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("owner", frozenset(DealerPermission)),
        (
            "admin",
            frozenset(
                {
                    DealerPermission.ORGANIZATION_SETTINGS_LIMITED,
                    DealerPermission.MEMBERSHIP_MANAGE,
                    DealerPermission.INVENTORY_MANAGE,
                    DealerPermission.REQUEST_METADATA_VIEW,
                    DealerPermission.INTEREST_MANAGE,
                    DealerPermission.CONVERSATION_ASSIGN,
                }
            ),
        ),
        ("inventory_manager", frozenset({DealerPermission.INVENTORY_MANAGE})),
        (
            "sales_agent",
            frozenset(
                {
                    DealerPermission.INTEREST_MANAGE,
                    DealerPermission.CONVERSATION_WORK_ASSIGNED,
                }
            ),
        ),
    ],
)
def test_dealer_role_permission_matrix(role: str, expected: frozenset[DealerPermission]) -> None:
    assert ROLE_PERMISSIONS[role] == expected
    assert AuthorizationPolicy().dealer_permissions(context(role=role)) == expected


@pytest.mark.parametrize(
    "override",
    [
        {"user_status": "suspended"},
        {"membership_status": "suspended"},
        {"membership_status": "revoked"},
        {"membership_status": "left"},
        {"organization_status": "suspended"},
        {"organization_verification_status": "pending"},
    ],
)
def test_dealer_access_denies_inactive_authorization_state(override: dict[str, str]) -> None:
    assert not AuthorizationPolicy().dealer_permissions(context(**override))


def test_organization_suspension_preserves_personal_capabilities() -> None:
    suspended_organization = context(organization_status="suspended")
    policy = AuthorizationPolicy()

    assert policy.can_buy(suspended_organization)
    assert policy.can_sell_personally(suspended_organization)
    assert not policy.dealer_permissions(suspended_organization)


def test_user_suspension_removes_personal_and_dealer_capabilities() -> None:
    suspended_user = context(user_status="suspended")
    policy = AuthorizationPolicy()

    assert not policy.can_buy(suspended_user)
    assert not policy.can_sell_personally(suspended_user)
    assert not policy.dealer_permissions(suspended_user)


def test_resource_policy_enforces_personal_owner_or_current_organization() -> None:
    policy = AuthorizationPolicy()
    actor = context(role="inventory_manager")

    assert policy.can_manage_owned_resource(
        actor,
        owner_user_id=USER_ID,
        owner_organization_id=None,
        dealer_permission=DealerPermission.INVENTORY_MANAGE,
    )
    assert policy.can_manage_owned_resource(
        actor,
        owner_user_id=None,
        owner_organization_id=ORG_ID,
        dealer_permission=DealerPermission.INVENTORY_MANAGE,
    )
    assert not policy.can_manage_owned_resource(
        actor,
        owner_user_id=UUID("018f0000-0000-7000-8000-000000000099"),
        owner_organization_id=None,
        dealer_permission=DealerPermission.INVENTORY_MANAGE,
    )
