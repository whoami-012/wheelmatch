from __future__ import annotations

from fastapi import Request
from redis.asyncio import Redis

from app.core.config import Settings
from app.modules.audit import AuditRecorder
from app.modules.authorization.cache import RedisAuthorizationCache
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.authorization.service import CapabilityService
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.repository import IdentityRepository
from app.modules.profiles.repository import ProfileRepository
from app.modules.profiles.service import ProfileService


def get_profile_service(request: Request) -> ProfileService:
    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis
    return ProfileService(
        repository=ProfileRepository(),
        identity_repository=IdentityRepository(),
        audit=AuditRecorder(),
        authorization_cache=RedisAuthorizationCache(
            redis, ttl_seconds=settings.authorization_cache_ttl_seconds
        ),
    )


def get_capability_service(request: Request) -> CapabilityService:
    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis
    return CapabilityService(
        identity_repository=IdentityRepository(),
        profile_repository=ProfileRepository(),
        dealer_repository=DealerRepository(),
        policy=AuthorizationPolicy(),
        cache=RedisAuthorizationCache(redis, ttl_seconds=settings.authorization_cache_ttl_seconds),
    )
