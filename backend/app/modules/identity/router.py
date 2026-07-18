from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.errors.models import ProblemDetail
from app.core.idempotency import canonical_request_hash
from app.modules.identity.dependencies import (
    get_authentication_rate_limiter,
    get_current_principal,
    get_identity_service,
    get_session_service,
)
from app.modules.identity.rate_limit import AuthenticationRateLimiter, RateLimitRule
from app.modules.identity.schemas import (
    LoginRequest,
    PasswordChangeRequest,
    RecoveryAcceptedResponse,
    RecoveryRequest,
    RecoveryResetRequest,
    RefreshRequest,
    RegisterRequest,
    RegistrationResponse,
    SessionResponse,
    TokenResponse,
    VerificationResponse,
    VerifyChallengeRequest,
)
from app.modules.identity.service import IdentityService
from app.modules.identity.session_service import CurrentPrincipal, SessionService

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
me_router = APIRouter(prefix="/api/v1/me", tags=["identity"])

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
IdentityServiceDependency = Annotated[IdentityService, Depends(get_identity_service)]
SessionServiceDependency = Annotated[SessionService, Depends(get_session_service)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
RateLimiterDependency = Annotated[
    AuthenticationRateLimiter, Depends(get_authentication_rate_limiter)
]
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
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
    429: {"model": ProblemDetail},
    503: {"model": ProblemDetail},
}


def _client_subject(request: Request) -> str:
    host = request.client.host if request.client is not None else "unknown"
    return f"ip:{host}"


@router.post("/login", response_model=TokenResponse, responses=_ERROR_RESPONSES)
async def login(
    request: Request,
    body: LoginRequest,
    session: SessionDependency,
    service: SessionServiceDependency,
    limiter: RateLimiterDependency,
) -> TokenResponse:
    settings: Settings = request.app.state.settings
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope="identity.login",
            limit=settings.login_rate_limit,
            window_seconds=900,
        ),
        subjects=[_client_subject(request), f"identity:{body.email.casefold().strip()}"],
    )
    return await service.login(session, body)


@router.post("/refresh", response_model=TokenResponse, responses=_ERROR_RESPONSES)
async def refresh(
    request: Request,
    body: RefreshRequest,
    session: SessionDependency,
    service: SessionServiceDependency,
    limiter: RateLimiterDependency,
) -> TokenResponse:
    settings: Settings = request.app.state.settings
    token = body.refresh_token.get_secret_value()
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope="identity.refresh",
            limit=settings.refresh_rate_limit,
            window_seconds=60,
        ),
        subjects=[_client_subject(request), f"credential:{token}"],
    )
    return await service.refresh(session, refresh_token=token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=_ERROR_RESPONSES)
async def logout(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: SessionServiceDependency,
) -> Response:
    await service.logout(session, principal=principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT, responses=_ERROR_RESPONSES)
async def logout_all(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: SessionServiceDependency,
) -> Response:
    await service.logout_all(session, principal=principal)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/password/change", status_code=status.HTTP_204_NO_CONTENT, responses=_ERROR_RESPONSES)
async def change_password(
    request: Request,
    body: PasswordChangeRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: SessionServiceDependency,
    limiter: RateLimiterDependency,
) -> Response:
    settings: Settings = request.app.state.settings
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope="identity.password.change",
            limit=settings.login_rate_limit,
            window_seconds=900,
        ),
        subjects=[_client_subject(request), f"user:{principal.user_id}"],
    )
    await service.change_password(
        session,
        principal=principal,
        current_password=body.current_password.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@me_router.get("/sessions", response_model=list[SessionResponse], responses=_ERROR_RESPONSES)
async def list_sessions(
    principal: PrincipalDependency,
    session: SessionDependency,
    service: SessionServiceDependency,
) -> list[SessionResponse]:
    return await service.list_sessions(session, principal=principal)


@me_router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERROR_RESPONSES,
)
async def revoke_session(
    session_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: SessionServiceDependency,
) -> Response:
    await service.revoke_session(
        session,
        principal=principal,
        family_id=session_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERROR_RESPONSES,
)
async def register(
    request: Request,
    body: RegisterRequest,
    idempotency_key: IdempotencyKey,
    session: SessionDependency,
    service: IdentityServiceDependency,
    limiter: RateLimiterDependency,
) -> RegistrationResponse:
    settings: Settings = request.app.state.settings
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope="identity.register",
            limit=settings.registration_rate_limit,
            window_seconds=3600,
        ),
        subjects=[_client_subject(request), f"identity:{body.email.casefold().strip()}"],
    )
    request_hash = canonical_request_hash(
        method="POST",
        path="/api/v1/auth/register",
        payload={
            "email": body.email,
            "phone": body.phone,
            "password": body.password.get_secret_value(),
            "display_name": body.display_name,
        },
    )
    return await service.register(
        session,
        body,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )


async def _verify(
    request: Request,
    body: VerifyChallengeRequest,
    *,
    expected_kind: str,
    session: AsyncSession,
    service: IdentityService,
    limiter: AuthenticationRateLimiter,
) -> VerificationResponse:
    settings: Settings = request.app.state.settings
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope=f"identity.verify.{expected_kind}",
            limit=settings.verification_rate_limit,
            window_seconds=900,
        ),
        subjects=[_client_subject(request), f"challenge:{body.challenge_id}"],
    )
    return await service.verify_challenge(
        session,
        challenge_id=body.challenge_id,
        code=body.code.get_secret_value(),
        expected_kind=expected_kind,
    )


@router.post(
    "/verify-email",
    response_model=VerificationResponse,
    responses=_ERROR_RESPONSES,
)
async def verify_email(
    request: Request,
    body: VerifyChallengeRequest,
    session: SessionDependency,
    service: IdentityServiceDependency,
    limiter: RateLimiterDependency,
) -> VerificationResponse:
    return await _verify(
        request,
        body,
        expected_kind="email",
        session=session,
        service=service,
        limiter=limiter,
    )


@router.post(
    "/verify-phone",
    response_model=VerificationResponse,
    responses=_ERROR_RESPONSES,
)
async def verify_phone(
    request: Request,
    body: VerifyChallengeRequest,
    session: SessionDependency,
    service: IdentityServiceDependency,
    limiter: RateLimiterDependency,
) -> VerificationResponse:
    return await _verify(
        request,
        body,
        expected_kind="phone",
        session=session,
        service=service,
        limiter=limiter,
    )


@router.post(
    "/recovery/request",
    response_model=RecoveryAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses=_ERROR_RESPONSES,
)
async def request_recovery(
    request: Request,
    body: RecoveryRequest,
    session: SessionDependency,
    service: IdentityServiceDependency,
    limiter: RateLimiterDependency,
) -> RecoveryAcceptedResponse:
    settings: Settings = request.app.state.settings
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope="identity.recovery.request",
            limit=settings.recovery_rate_limit,
            window_seconds=3600,
        ),
        subjects=[_client_subject(request), f"identity:{body.email.casefold().strip()}"],
    )
    return await service.request_recovery(session, email=body.email)


@router.post(
    "/recovery/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_ERROR_RESPONSES,
)
async def reset_password(
    request: Request,
    body: RecoveryResetRequest,
    session: SessionDependency,
    service: IdentityServiceDependency,
    limiter: RateLimiterDependency,
) -> Response:
    settings: Settings = request.app.state.settings
    await limiter.enforce(
        session,
        rule=RateLimitRule(
            scope="identity.recovery.reset",
            limit=settings.recovery_rate_limit,
            window_seconds=3600,
        ),
        subjects=[_client_subject(request), f"credential:{body.token.get_secret_value()}"],
    )
    await service.reset_password(
        session,
        token=body.token.get_secret_value(),
        new_password=body.new_password.get_secret_value(),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
