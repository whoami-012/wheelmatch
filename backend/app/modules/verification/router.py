from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.errors.models import ProblemDetail
from app.core.idempotency import canonical_request_hash
from app.modules.identity.dependencies import (
    get_authentication_rate_limiter,
    get_current_principal,
)
from app.modules.identity.rate_limit import AuthenticationRateLimiter, RateLimitRule
from app.modules.identity.session_service import CurrentPrincipal
from app.modules.verification.dependencies import get_identity_verification_service
from app.modules.verification.schemas import (
    IdentityVerificationStartResponse,
    IdentityVerificationStatusResponse,
)
from app.modules.verification.service import IdentityVerificationService

router = APIRouter(prefix="/api/v1/me", tags=["identity verification"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
ServiceDependency = Annotated[
    IdentityVerificationService, Depends(get_identity_verification_service)
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
    "/identity-verifications",
    response_model=IdentityVerificationStartResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
async def start_identity_verification(
    request: Request,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
    limiter: LimiterDependency,
) -> IdentityVerificationStartResponse:
    settings: Settings = request.app.state.settings
    host = request.client.host if request.client is not None else "unknown"
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope="identity.verification.start",
            limit=settings.verification_rate_limit,
            window_seconds=900,
        ),
        subjects=[f"ip:{host}", f"user:{principal.user_id}"],
    )
    return await service.start(
        session,
        actor_user_id=principal.user_id,
        idempotency_key=idempotency_key,
        request_hash=canonical_request_hash(
            method="POST", path="/api/v1/me/identity-verifications", payload={}
        ),
    )


@router.get(
    "/identity-verification",
    response_model=IdentityVerificationStatusResponse,
    responses=_ERRORS,
)
async def get_identity_verification_status(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> IdentityVerificationStatusResponse:
    return await service.status(session, actor_user_id=principal.user_id)
