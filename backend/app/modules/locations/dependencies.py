from fastapi import Request

from app.modules.audit import AuditRecorder
from app.modules.listings.dependencies import get_listing_service
from app.modules.locations.repository import LocationRepository
from app.modules.locations.service import LocationService


def get_location_service(request: Request) -> LocationService:
    return LocationService(
        repository=LocationRepository(),
        listing_service=get_listing_service(request),
        audit=AuditRecorder(),
    )
