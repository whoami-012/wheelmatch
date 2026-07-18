from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors.models import ProblemDetail
from app.core.idempotency import canonical_request_hash
from app.modules.dealers.dependencies import get_dealer_service
from app.modules.dealers.schemas import (
    MembershipAcceptRequest,
    MembershipInviteRequest,
    MembershipListItem,
    MembershipResponse,
    MembershipUpdateRequest,
    OrganizationCreateRequest,
    OrganizationResponse,
)
from app.modules.dealers.service import DealerService
from app.modules.identity.dependencies import get_current_principal
from app.modules.identity.session_service import CurrentPrincipal

router = APIRouter(prefix="/api/v1/dealer-organizations", tags=["dealers"])
me_router = APIRouter(prefix="/api/v1/me/dealer-memberships", tags=["dealers"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
DealerServiceDependency = Annotated[DealerService, Depends(get_dealer_service)]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=16,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    ),
]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ProblemDetail},
    401: {"model": ProblemDetail},
    403: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
}


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
async def create_organization(
    body: OrganizationCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: DealerServiceDependency,
) -> OrganizationResponse:
    request_hash = canonical_request_hash(
        method="POST",
        path="/api/v1/dealer-organizations",
        payload=body.model_dump(mode="json"),
    )
    return await service.create_organization(
        session,
        actor_user_id=principal.user_id,
        request=body,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )


@router.get("/{organization_id}", response_model=OrganizationResponse, responses=_ERROR_RESPONSES)
async def get_organization(
    organization_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: DealerServiceDependency,
) -> OrganizationResponse:
    return await service.get_organization(
        session,
        actor_user_id=principal.user_id,
        organization_id=organization_id,
    )


@router.post(
    "/{organization_id}/memberships",
    response_model=MembershipResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
async def invite_member(
    organization_id: UUID,
    body: MembershipInviteRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: DealerServiceDependency,
) -> MembershipResponse:
    request_hash = canonical_request_hash(
        method="POST",
        path=f"/api/v1/dealer-organizations/{organization_id}/memberships",
        payload=body.model_dump(mode="json"),
    )
    return await service.invite_member(
        session,
        actor_user_id=principal.user_id,
        organization_id=organization_id,
        target_user_id=body.user_id,
        role=body.role,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )


@router.patch(
    "/{organization_id}/memberships/{membership_id}",
    response_model=MembershipResponse,
    responses=_ERROR_RESPONSES,
)
async def update_membership(
    organization_id: UUID,
    membership_id: UUID,
    body: MembershipUpdateRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: DealerServiceDependency,
) -> MembershipResponse:
    return await service.update_membership(
        session,
        actor_user_id=principal.user_id,
        organization_id=organization_id,
        membership_id=membership_id,
        request=body,
    )


@me_router.get("", response_model=list[MembershipListItem], responses=_ERROR_RESPONSES)
async def list_memberships(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: DealerServiceDependency,
) -> list[MembershipListItem]:
    return await service.list_memberships(session, actor_user_id=principal.user_id)


@me_router.post(
    "/{membership_id}/accept",
    response_model=MembershipResponse,
    responses=_ERROR_RESPONSES,
)
async def accept_invitation(
    membership_id: UUID,
    body: MembershipAcceptRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: DealerServiceDependency,
) -> MembershipResponse:
    return await service.accept_invitation(
        session,
        actor_user_id=principal.user_id,
        membership_id=membership_id,
        invitation_token=body.invitation_token.get_secret_value(),
    )


@me_router.post(
    "/{membership_id}/leave",
    response_model=MembershipResponse,
    responses=_ERROR_RESPONSES,
)
async def leave_organization(
    membership_id: UUID,
    expected_version: Annotated[int, Query(ge=1)],
    principal: PrincipalDependency,
    session: SessionDependency,
    service: DealerServiceDependency,
) -> MembershipResponse:
    return await service.leave_organization(
        session,
        actor_user_id=principal.user_id,
        membership_id=membership_id,
        expected_version=expected_version,
    )
