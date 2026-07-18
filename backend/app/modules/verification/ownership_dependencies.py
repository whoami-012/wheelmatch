from fastapi import Request

from app.core.config.settings import (
    OwnershipVerificationProviderName,
    VehicleIdentityNormalizerName,
)
from app.core.idempotency import IdempotencyRepository
from app.modules.audit import AuditRecorder
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.repository import ListingRepository
from app.modules.verification.ownership_provider import (
    DeterministicOwnershipVerificationProvider,
    DisabledOwnershipVerificationProvider,
    OwnershipVerificationProvider,
)
from app.modules.verification.ownership_repository import OwnershipVerificationRepository
from app.modules.verification.ownership_reuse import OwnershipReusePolicy
from app.modules.verification.ownership_service import OwnershipVerificationService
from app.modules.verification.repository import VerificationRepository
from app.modules.verification.vehicle_identity import (
    DeterministicVehicleIdentityNormalizer,
    DisabledVehicleIdentityNormalizer,
    VehicleIdentityNormalizer,
)


def get_ownership_verification_service(request: Request) -> OwnershipVerificationService:
    settings = request.app.state.settings
    provider: OwnershipVerificationProvider
    normalizer: VehicleIdentityNormalizer
    if settings.ownership_verification_provider is OwnershipVerificationProviderName.DETERMINISTIC:
        provider = DeterministicOwnershipVerificationProvider()
    else:
        provider = DisabledOwnershipVerificationProvider()
    if settings.vehicle_identity_normalizer is VehicleIdentityNormalizerName.DETERMINISTIC:
        normalizer = DeterministicVehicleIdentityNormalizer()
    else:
        normalizer = DisabledVehicleIdentityNormalizer()
    return OwnershipVerificationService(
        repository=OwnershipVerificationRepository(),
        listing_repository=ListingRepository(),
        identity_repository=IdentityRepository(),
        dealer_repository=DealerRepository(),
        authorization_policy=AuthorizationPolicy(),
        identity_verification_repository=VerificationRepository(),
        provider=provider,
        normalizer=normalizer,
        hmac_key=settings.vehicle_identity_hmac_key.get_secret_value().encode(),
        hash_version=settings.vehicle_identity_hash_version,
        reuse_policy=OwnershipReusePolicy(
            freshness_days=settings.ownership_reuse_freshness_days,
            policy_version=settings.ownership_reuse_policy_version,
        ),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
    )
