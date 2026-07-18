from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.outbox import enqueue_event
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.listings.service import ListingService
from app.modules.locations.models import ListingLocation
from app.modules.locations.repository import LocationRepository
from app.modules.locations.schemas import ListingLocationProjection, ListingLocationWriteRequest


class LocationService:
    def __init__(
        self,
        *,
        repository: LocationRepository,
        listing_service: ListingService,
        audit: AuditRecorder,
    ) -> None:
        self._repository = repository
        self._listings = listing_service
        self._audit = audit

    async def write(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        listing_id: UUID,
        request: ListingLocationWriteRequest,
    ) -> ListingLocationProjection:
        async with session.begin():
            listing, actor, membership_id = await self._listings.get_authorized_listing(
                session,
                actor_user_id=actor_user_id,
                listing_id=listing_id,
                for_update=True,
            )
            if listing.lifecycle_status != "draft":
                raise AppError(
                    status=409, code="LISTING_NOT_EDITABLE", title="Listing is not editable"
                )
            if listing.version != request.expected_version:
                raise AppError(
                    status=409, code="LISTING_VERSION_CONFLICT", title="Listing state changed"
                )
            public_address = None
            if request.visibility == "public_business":
                if listing.owner_organization_id is None or request.public_address_id is None:
                    raise AppError(
                        status=422,
                        code="PUBLIC_BUSINESS_LOCATION_INVALID",
                        title="Public business location is not permitted",
                    )
                public_address = await self._repository.get_public_address(
                    session, request.public_address_id
                )
                if (
                    public_address is None
                    or public_address.organization_id != listing.owner_organization_id
                    or public_address.verified_at is None
                    or public_address.publication_status != "published"
                ):
                    raise AppError(
                        status=422,
                        code="PUBLIC_BUSINESS_LOCATION_INVALID",
                        title="Public business location is not permitted",
                    )
            elif request.public_address_id is not None:
                raise AppError(
                    status=422,
                    code="PUBLIC_BUSINESS_LOCATION_INVALID",
                    title="Public business location is not permitted",
                )
            await self._repository.upsert(
                session,
                listing_id=listing.id,
                latitude=request.latitude,
                longitude=request.longitude,
                locality=request.locality,
                coarse_area=request.coarse_area,
                visibility=request.visibility,
                public_address_id=request.public_address_id,
            )
            listing.version += 1
            self._audit.record(
                session,
                action="listing.location.updated",
                outcome="success",
                resource_type="listing",
                actor_user_id=actor.id,
                organization_id=listing.owner_organization_id,
                membership_id=membership_id,
                resource_id=listing.id,
                changes={"visibility": request.visibility, "version": listing.version},
                request_id=get_request_id(),
            )
            enqueue_event(
                session,
                event_type="listing.location.updated",
                aggregate_type="listing",
                aggregate_id=listing.id,
                payload={"visibility": request.visibility, "version": listing.version},
            )
            await session.flush()
            location = await self._repository.get(session, listing.id)
            if location is None:
                raise AssertionError("location upsert completed without a row")
            return self._projection(
                location, listing.version, public_address.address_line if public_address else None
            )

    async def get(
        self, session: AsyncSession, *, actor_user_id: UUID, listing_id: UUID
    ) -> ListingLocationProjection:
        listing, _, _ = await self._listings.get_authorized_listing(
            session, actor_user_id=actor_user_id, listing_id=listing_id
        )
        location = await self._repository.get(session, listing.id)
        if location is None:
            raise AppError(
                status=404, code="LISTING_LOCATION_NOT_FOUND", title="Listing location not found"
            )
        address = None
        if location.public_address_id is not None:
            public_address = await self._repository.get_public_address(
                session, location.public_address_id
            )
            address = public_address.address_line if public_address else None
        return self._projection(location, listing.version, address)

    @staticmethod
    def _projection(
        location: ListingLocation, listing_version: int, public_address: str | None
    ) -> ListingLocationProjection:
        return ListingLocationProjection(
            locality=location.locality,
            coarse_area=location.coarse_area,
            visibility=location.visibility,
            public_business_address=public_address
            if location.visibility == "public_business"
            else None,
            listing_version=listing_version,
        )
