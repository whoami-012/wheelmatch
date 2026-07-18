from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.modules.catalogue.repository import CatalogueRepository
from app.modules.catalogue.schemas import CataloguePage, CatalogueSearchPage, VehicleType
from app.modules.catalogue.service import CatalogueService
from app.modules.identity.dependencies import get_current_principal
from app.modules.identity.session_service import CurrentPrincipal

router = APIRouter(prefix="/api/v1/catalogue", tags=["catalogue"])


def get_catalogue_service() -> CatalogueService:
    return CatalogueService(CatalogueRepository())


SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
ServiceDependency = Annotated[CatalogueService, Depends(get_catalogue_service)]


@router.get("/makes", response_model=CataloguePage)
async def list_makes(
    _principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
    vehicle_type: VehicleType,
    query: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> CataloguePage:
    return await service.list_makes(session, vehicle_type=vehicle_type, query=query, limit=limit)


@router.get("/models", response_model=CataloguePage)
async def list_models(
    _principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
    make_id: UUID,
    vehicle_type: VehicleType,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> CataloguePage:
    return await service.list_models(
        session, make_id=make_id, vehicle_type=vehicle_type, limit=limit
    )


@router.get("/variants", response_model=CataloguePage)
async def list_variants(
    _principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
    model_id: UUID,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> CataloguePage:
    return await service.list_variants(session, model_id=model_id, limit=limit)


@router.get("/search", response_model=CatalogueSearchPage)
async def search_catalogue(
    _principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
    vehicle_type: VehicleType,
    query: Annotated[str, Query(min_length=1, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> CatalogueSearchPage:
    return await service.search(session, vehicle_type=vehicle_type, query=query, limit=limit)
