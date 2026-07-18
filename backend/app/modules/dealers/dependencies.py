from __future__ import annotations

from fastapi import Request
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.idempotency import IdempotencyRepository
from app.core.security import SecretHasher
from app.modules.audit import AuditRecorder
from app.modules.authorization.cache import RedisAuthorizationCache
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.dealers.delivery import NullDealerInvitationDelivery
from app.modules.dealers.repository import DealerRepository
from app.modules.dealers.service import DealerService
from app.modules.identity.repository import IdentityRepository


def get_dealer_service(request: Request) -> DealerService:
    settings: Settings = request.app.state.settings
    redis: Redis = request.app.state.redis
    return DealerService(
        repository=DealerRepository(),
        identity_repository=IdentityRepository(),
        policy=AuthorizationPolicy(),
        secret_hasher=SecretHasher(settings.secret_hash_key.get_secret_value()),
        audit=AuditRecorder(),
        delivery=NullDealerInvitationDelivery(),
        authorization_cache=RedisAuthorizationCache(
            redis, ttl_seconds=settings.authorization_cache_ttl_seconds
        ),
        idempotency_repository=IdempotencyRepository(),
        invitation_ttl_seconds=settings.dealer_invitation_ttl_seconds,
    )
