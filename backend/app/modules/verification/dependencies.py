from fastapi import Request

from app.core.config.settings import IdentityVerificationProviderName
from app.core.idempotency import IdempotencyRepository
from app.modules.audit import AuditRecorder
from app.modules.identity.repository import IdentityRepository
from app.modules.verification.provider import (
    DeterministicIdentityVerificationProvider,
    DisabledIdentityVerificationProvider,
    IdentityVerificationProvider,
)
from app.modules.verification.repository import VerificationRepository
from app.modules.verification.service import IdentityVerificationService


def get_identity_verification_service(request: Request) -> IdentityVerificationService:
    provider: IdentityVerificationProvider
    if (
        request.app.state.settings.identity_verification_provider
        is IdentityVerificationProviderName.DETERMINISTIC
    ):
        provider = DeterministicIdentityVerificationProvider()
    else:
        provider = DisabledIdentityVerificationProvider()
    return IdentityVerificationService(
        repository=VerificationRepository(),
        identity_repository=IdentityRepository(),
        provider=provider,
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
    )
