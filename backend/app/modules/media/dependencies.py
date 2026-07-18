from fastapi import Request

from app.core.idempotency import IdempotencyRepository
from app.modules.audit import AuditRecorder
from app.modules.listings.dependencies import get_listing_service
from app.modules.media.repository import MediaRepository
from app.modules.media.service import MediaService
from app.modules.media.storage import MediaStorage


def get_media_service(request: Request) -> MediaService:
    return MediaService(
        repository=MediaRepository(),
        listing_service=get_listing_service(request),
        storage=MediaStorage(request.app.state.settings),
        audit=AuditRecorder(),
        idempotency_repository=IdempotencyRepository(),
    )
