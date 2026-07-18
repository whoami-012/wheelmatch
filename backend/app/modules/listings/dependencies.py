from fastapi import Request

from app.core.idempotency import IdempotencyRepository
from app.modules.audit import AuditRecorder
from app.modules.authorization.policy import AuthorizationPolicy
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.cursors import ListingCursorCodec
from app.modules.listings.readiness import ReadinessPolicy
from app.modules.listings.repository import ListingRepository
from app.modules.listings.service import ListingService
from app.modules.listings.submission_repository import ListingSubmissionRepository
from app.modules.listings.submission_service import ListingSubmissionService
from app.modules.locations.repository import LocationRepository
from app.modules.media.repository import MediaRepository
from app.modules.profiles.repository import ProfileRepository
from app.modules.verification.ownership_repository import OwnershipVerificationRepository
from app.modules.verification.ownership_reuse import OwnershipReusePolicy
from app.modules.verification.repository import VerificationRepository


def get_listing_service(_request: Request) -> ListingService:
    return ListingService(
        repository=ListingRepository(),
        identity_repository=IdentityRepository(),
        dealer_repository=DealerRepository(),
        policy=AuthorizationPolicy(),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
        cursor_codec=ListingCursorCodec(
            _request.app.state.settings.secret_hash_key.get_secret_value()
        ),
    )


def get_listing_submission_service(_request: Request) -> ListingSubmissionService:
    listing_repository = ListingRepository()
    settings = _request.app.state.settings
    return ListingSubmissionService(
        listing_service=ListingService(
            repository=listing_repository,
            identity_repository=IdentityRepository(),
            dealer_repository=DealerRepository(),
            policy=AuthorizationPolicy(),
            audit=AuditRecorder(),
            idempotency_repository=IdempotencyRepository(),
            cursor_codec=ListingCursorCodec(
                _request.app.state.settings.secret_hash_key.get_secret_value()
            ),
        ),
        listing_repository=listing_repository,
        submission_repository=ListingSubmissionRepository(),
        profile_repository=ProfileRepository(),
        location_repository=LocationRepository(),
        media_repository=MediaRepository(),
        verification_repository=VerificationRepository(),
        ownership_repository=OwnershipVerificationRepository(),
        ownership_reuse_policy=OwnershipReusePolicy(
            freshness_days=settings.ownership_reuse_freshness_days,
            policy_version=settings.ownership_reuse_policy_version,
        ),
        policy=ReadinessPolicy(),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
    )
