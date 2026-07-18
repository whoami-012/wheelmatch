from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors.models import ProblemDetail
from app.modules.authorization.service import CapabilityService
from app.modules.identity.dependencies import get_current_principal
from app.modules.identity.session_service import CurrentPrincipal
from app.modules.profiles.dependencies import get_capability_service, get_profile_service
from app.modules.profiles.schemas import (
    CapabilitiesResponse,
    ProfileResponse,
    ProfileUpdateRequest,
    SellerProfileCreateRequest,
    SellerProfileResponse,
)
from app.modules.profiles.service import ProfileService

router = APIRouter(prefix="/api/v1/me", tags=["profiles"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
ProfileServiceDependency = Annotated[ProfileService, Depends(get_profile_service)]
CapabilityServiceDependency = Annotated[CapabilityService, Depends(get_capability_service)]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
}


@router.get("/profile", response_model=ProfileResponse, responses=_ERROR_RESPONSES)
async def get_profile(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ProfileServiceDependency,
) -> ProfileResponse:
    return await service.get_profile(session, actor_user_id=principal.user_id)


@router.patch("/profile", response_model=ProfileResponse, responses=_ERROR_RESPONSES)
async def update_profile(
    body: ProfileUpdateRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ProfileServiceDependency,
) -> ProfileResponse:
    return await service.update_profile(
        session,
        actor_user_id=principal.user_id,
        request=body,
    )


@router.post(
    "/seller-profile",
    response_model=SellerProfileResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
async def create_seller_profile(
    _body: SellerProfileCreateRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ProfileServiceDependency,
) -> SellerProfileResponse:
    return await service.create_seller_profile(session, actor_user_id=principal.user_id)


@router.get("/seller-readiness", response_model=SellerProfileResponse, responses=_ERROR_RESPONSES)
async def get_seller_readiness(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ProfileServiceDependency,
) -> SellerProfileResponse:
    return await service.get_seller_readiness(session, actor_user_id=principal.user_id)


@router.get("/capabilities", response_model=CapabilitiesResponse, responses=_ERROR_RESPONSES)
async def get_capabilities(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: CapabilityServiceDependency,
) -> CapabilitiesResponse:
    return await service.get_capabilities(session, actor_user_id=principal.user_id)
