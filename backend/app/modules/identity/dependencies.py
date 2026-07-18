from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.database import get_session
from app.core.idempotency import IdempotencyRepository
from app.core.security import AccessTokenService, PasswordService, SecretHasher
from app.modules.audit import AuditRecorder
from app.modules.authorization.cache import RedisAuthorizationCache
from app.modules.identity.delivery import NullIdentityChallengeDelivery
from app.modules.identity.rate_limit import AuthenticationRateLimiter
from app.modules.identity.repository import IdentityRepository
from app.modules.identity.service import IdentityService
from app.modules.identity.session_service import CurrentPrincipal, SessionService

_bearer = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_password_service() -> PasswordService:
    return PasswordService()


def get_access_token_service(request: Request) -> AccessTokenService:
    settings: Settings = request.app.state.settings
    return AccessTokenService(
        signing_key=settings.access_token_signing_key.get_secret_value(),
        issuer=settings.access_token_issuer,
        audience=settings.access_token_audience,
        ttl_seconds=settings.access_token_ttl_seconds,
    )


def get_identity_service(request: Request) -> IdentityService:
    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis
    secret_hasher = SecretHasher(settings.secret_hash_key.get_secret_value())
    return IdentityService(
        repository=IdentityRepository(),
        password_service=get_password_service(),
        secret_hasher=secret_hasher,
        audit=AuditRecorder(),
        delivery=NullIdentityChallengeDelivery(),
        authorization_cache=RedisAuthorizationCache(
            redis, ttl_seconds=settings.authorization_cache_ttl_seconds
        ),
        idempotency_repository=IdempotencyRepository(),
        verification_ttl_seconds=settings.verification_challenge_ttl_seconds,
        recovery_ttl_seconds=settings.recovery_challenge_ttl_seconds,
    )


def get_session_service(request: Request) -> SessionService:
    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis
    return SessionService(
        repository=IdentityRepository(),
        password_service=get_password_service(),
        secret_hasher=SecretHasher(settings.secret_hash_key.get_secret_value()),
        access_tokens=get_access_token_service(request),
        audit=AuditRecorder(),
        authorization_cache=RedisAuthorizationCache(
            redis, ttl_seconds=settings.authorization_cache_ttl_seconds
        ),
        refresh_ttl_seconds=settings.refresh_session_ttl_seconds,
        login_failure_threshold=settings.login_failure_threshold,
        login_lock_seconds=settings.login_lock_seconds,
    )


async def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
    service: Annotated[SessionService, Depends(get_session_service)],
) -> CurrentPrincipal:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise SessionService._session_invalid_error()
    return await service.authenticate_access_token(session, access_token=credentials.credentials)


def get_authentication_rate_limiter(request: Request) -> AuthenticationRateLimiter:
    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis
    return AuthenticationRateLimiter(
        redis,
        secret_hasher=SecretHasher(settings.secret_hash_key.get_secret_value()),
    )
