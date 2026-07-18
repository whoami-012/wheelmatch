from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.errors.models import ProblemDetail
from app.modules.identity.dependencies import (
    get_authentication_rate_limiter,
    get_current_principal,
)
from app.modules.identity.rate_limit import AuthenticationRateLimiter, RateLimitRule
from app.modules.identity.session_service import CurrentPrincipal
from app.modules.verification.ownership_dependencies import get_ownership_verification_service
from app.modules.verification.ownership_schemas import (
    OwnershipVerificationStartRequest,
    OwnershipVerificationStartResponse,
    OwnershipVerificationStatusResponse,
)
from app.modules.verification.ownership_service import OwnershipVerificationService

router = APIRouter(prefix="/api/v1/listings", tags=["ownership verification"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
ServiceDependency = Annotated[
    OwnershipVerificationService, Depends(get_ownership_verification_service)
]
LimiterDependency = Annotated[AuthenticationRateLimiter, Depends(get_authentication_rate_limiter)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
    429: {"model": ProblemDetail},
    503: {"model": ProblemDetail},
}


@router.post(
    "/{listing_id}/ownership-verification/start",
    response_model=OwnershipVerificationStartResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
async def start_ownership_verification(
    request_context: Request,
    listing_id: UUID,
    request: OwnershipVerificationStartRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
    limiter: LimiterDependency,
) -> OwnershipVerificationStartResponse:
    settings: Settings = request_context.app.state.settings
    host = request_context.client.host if request_context.client is not None else "unknown"
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope="ownership.verification.start",
            limit=settings.verification_rate_limit,
            window_seconds=900,
        ),
        subjects=[f"ip:{host}", f"user:{principal.user_id}"],
    )
    return await service.start(
        session,
        actor_user_id=principal.user_id,
        listing_id=listing_id,
        request=request,
        idempotency_key=idempotency_key,
    )


@router.get(
    "/{listing_id}/ownership-verification/status",
    response_model=OwnershipVerificationStatusResponse,
    responses=_ERRORS,
)
async def get_ownership_verification_status(
    listing_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> OwnershipVerificationStatusResponse:
    return await service.status(session, actor_user_id=principal.user_id, listing_id=listing_id)
