from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors.models import ProblemDetail
from app.core.idempotency import canonical_request_hash
from app.modules.identity.dependencies import get_current_principal
from app.modules.identity.session_service import CurrentPrincipal
from app.modules.listings.dependencies import get_listing_service, get_listing_submission_service
from app.modules.listings.schemas import (
    ListingCreateRequest,
    ListingPageResponse,
    ListingPrivateResponse,
    ListingUpdateRequest,
)
from app.modules.listings.service import ListingService
from app.modules.listings.submission_schemas import (
    ListingSubmissionRequest,
    ListingSubmissionResponse,
    PublicationReadinessResponse,
)
from app.modules.listings.submission_service import ListingSubmissionService

router = APIRouter(prefix="/api/v1/listings", tags=["listings"])
me_router = APIRouter(prefix="/api/v1/me/listings", tags=["listings"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
ServiceDependency = Annotated[ListingService, Depends(get_listing_service)]
SubmissionServiceDependency = Annotated[
    ListingSubmissionService, Depends(get_listing_submission_service)
]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetail},
    403: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
}


@router.post(
    "",
    response_model=ListingPrivateResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
async def create_listing(
    body: ListingCreateRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> ListingPrivateResponse:
    return await service.create(
        session,
        actor_user_id=principal.user_id,
        request=body,
        idempotency_key=idempotency_key,
        request_hash=canonical_request_hash(
            method="POST", path="/api/v1/listings", payload=body.model_dump(mode="json")
        ),
    )


@router.get("/{listing_id}", response_model=ListingPrivateResponse, responses=_ERROR_RESPONSES)
async def get_listing(
    listing_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> ListingPrivateResponse:
    return await service.get(session, actor_user_id=principal.user_id, listing_id=listing_id)


@router.patch("/{listing_id}", response_model=ListingPrivateResponse, responses=_ERROR_RESPONSES)
async def update_listing(
    listing_id: UUID,
    body: ListingUpdateRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> ListingPrivateResponse:
    return await service.update(
        session, actor_user_id=principal.user_id, listing_id=listing_id, request=body
    )


@router.post(
    "/{listing_id}/submit",
    response_model=ListingSubmissionResponse,
    responses=_ERROR_RESPONSES,
)
async def submit_listing(
    listing_id: UUID,
    body: ListingSubmissionRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: SubmissionServiceDependency,
) -> ListingSubmissionResponse:
    return await service.submit(
        session,
        actor_user_id=principal.user_id,
        listing_id=listing_id,
        request=body,
        idempotency_key=idempotency_key,
        request_hash=canonical_request_hash(
            method="POST",
            path=f"/api/v1/listings/{listing_id}/submit",
            payload=body.model_dump(mode="json"),
        ),
    )


@router.get(
    "/{listing_id}/publication-readiness",
    response_model=PublicationReadinessResponse,
    responses=_ERROR_RESPONSES,
)
async def get_publication_readiness(
    listing_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: SubmissionServiceDependency,
) -> PublicationReadinessResponse:
    return await service.readiness(session, actor_user_id=principal.user_id, listing_id=listing_id)


@me_router.get("", response_model=ListingPageResponse, responses=_ERROR_RESPONSES)
async def list_owned_listings(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
    owner_type: Annotated[str, Query(pattern=r"^(personal|dealer_organization)$")] = "personal",
    organization_id: UUID | None = None,
    lifecycle_status: Annotated[str, Query(pattern=r"^(draft|removed)$")] = "draft",
    cursor: Annotated[str | None, Query(min_length=16, max_length=1024)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> ListingPageResponse:
    return await service.list_owned(
        session,
        actor_user_id=principal.user_id,
        owner_type=owner_type,
        organization_id=organization_id,
        lifecycle_status=lifecycle_status,
        cursor=cursor,
        limit=limit,
    )
