from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors.models import ProblemDetail
from app.modules.identity.dependencies import get_current_principal
from app.modules.identity.session_service import CurrentPrincipal
from app.modules.locations.dependencies import get_location_service
from app.modules.locations.schemas import ListingLocationProjection, ListingLocationWriteRequest
from app.modules.locations.service import LocationService

router = APIRouter(prefix="/api/v1/listings", tags=["locations"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
ServiceDependency = Annotated[LocationService, Depends(get_location_service)]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
}


@router.put("/{listing_id}/location", response_model=ListingLocationProjection, responses=_ERRORS)
async def write_location(
    listing_id: UUID,
    body: ListingLocationWriteRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> ListingLocationProjection:
    return await service.write(
        session,
        actor_user_id=principal.user_id,
        listing_id=listing_id,
        request=body,
    )


@router.get("/{listing_id}/location", response_model=ListingLocationProjection, responses=_ERRORS)
async def get_location(
    listing_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> ListingLocationProjection:
    return await service.get(session, actor_user_id=principal.user_id, listing_id=listing_id)
