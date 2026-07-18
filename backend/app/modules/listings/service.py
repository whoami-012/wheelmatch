from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.idempotency import IdempotencyConflictError, IdempotencyRepository
from app.core.ids import uuid7
from app.core.outbox import enqueue_event
from app.core.telemetry.context import get_request_id
from app.modules.audit import AuditRecorder
from app.modules.authorization.policy import (
    AuthorizationContext,
    AuthorizationPolicy,
    DealerPermission,
    OrganizationAccess,
)
from app.modules.dealers.repository import DealerRepository
from app.modules.identity.models import User
from app.modules.identity.repository import IdentityRepository
from app.modules.listings.cursors import ListingCursor, ListingCursorCodec
from app.modules.listings.models import BikeSpec, CarSpec, Listing, VehicleSpec
from app.modules.listings.repository import ListingRepository
from app.modules.listings.schemas import (
    DealerOwnerContext,
    ListingCreateRequest,
    ListingPageResponse,
    ListingPrivateResponse,
    ListingUpdateRequest,
)


class ListingService:
    def __init__(
        self,
        *,
        repository: ListingRepository,
        identity_repository: IdentityRepository,
        dealer_repository: DealerRepository,
        policy: AuthorizationPolicy,
        audit: AuditRecorder,
        idempotency_repository: IdempotencyRepository,
        cursor_codec: ListingCursorCodec,
    ) -> None:
        self._repository = repository
        self._identities = identity_repository
        self._dealers = dealer_repository
        self._policy = policy
        self._audit = audit
        self._idempotency = idempotency_repository
        self._cursors = cursor_codec

    async def create(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        request: ListingCreateRequest,
        idempotency_key: str,
        request_hash: str,
    ) -> ListingPrivateResponse:
        now = datetime.now(UTC)
        async with session.begin():
            try:
                reservation = await self._idempotency.reserve(
                    session,
                    scope=f"user:{actor_user_id}",
                    operation="listing.draft.create",
                    key=idempotency_key,
                    request_hash=request_hash,
                    expires_at=now + timedelta(hours=24),
                )
            except IdempotencyConflictError as exc:
                raise AppError(
                    status=409,
                    code="IDEMPOTENCY_KEY_CONFLICT",
                    title="Idempotency key conflicts with an earlier request",
                ) from exc
            if not reservation.acquired:
                if reservation.replay_body is None:
                    raise AppError(
                        status=409,
                        code="IDEMPOTENCY_IN_PROGRESS",
                        title="An idempotent request is already in progress",
                    )
                return ListingPrivateResponse.model_validate(reservation.replay_body)

            actor = await self._identities.get_user_by_id(session, actor_user_id, for_update=True)
            if actor is None:
                raise self.listing_not_found()
            (
                owner_type,
                owner_user_id,
                owner_organization_id,
                membership_id,
            ) = await self._creation_owner(session, actor=actor, request=request)
            if request.variant_id is not None and not await self._repository.variant_matches_type(
                session, variant_id=request.variant_id, vehicle_type=request.vehicle_type
            ):
                raise AppError(
                    status=422,
                    code="CATALOGUE_VARIANT_INVALID",
                    title="Vehicle variant is not valid for the selected vehicle type",
                )
            listing = Listing(
                id=uuid7(),
                owner_type=owner_type,
                owner_user_id=owner_user_id,
                owner_organization_id=owner_organization_id,
                created_by_user_id=actor.id,
                vehicle_type=request.vehicle_type,
                variant_id=request.variant_id,
                lifecycle_status="draft",
            )
            session.add(listing)
            await session.flush()
            self._audit.record(
                session,
                action="listing.draft.created",
                outcome="success",
                resource_type="listing",
                actor_user_id=actor.id,
                organization_id=owner_organization_id,
                membership_id=membership_id,
                resource_id=listing.id,
                changes={"owner_type": owner_type, "vehicle_type": listing.vehicle_type},
                request_id=get_request_id(),
            )
            enqueue_event(
                session,
                event_type="listing.draft.created",
                aggregate_type="listing",
                aggregate_id=listing.id,
                payload={
                    "owner_type": owner_type,
                    "vehicle_type": listing.vehicle_type,
                    "version": listing.version,
                },
            )
            response = await self._response(session, listing)
            await self._idempotency.complete(
                session,
                scope=f"user:{actor_user_id}",
                operation="listing.draft.create",
                key=idempotency_key,
                response_status=201,
                response_body=response.model_dump(mode="json"),
                resource_type="listing",
                resource_id=listing.id,
            )
        return response

    async def get(
        self, session: AsyncSession, *, actor_user_id: UUID, listing_id: UUID
    ) -> ListingPrivateResponse:
        listing, _, _ = await self.get_authorized_listing(
            session, actor_user_id=actor_user_id, listing_id=listing_id
        )
        return await self._response(session, listing)

    async def get_authorized_listing(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        listing_id: UUID,
        for_update: bool = False,
    ) -> tuple[Listing, User, UUID | None]:
        listing = await self._repository.get(session, listing_id, for_update=for_update)
        if listing is None:
            raise self.listing_not_found()
        actor, membership_id = await self._authorize(
            session, actor_user_id=actor_user_id, listing=listing
        )
        return listing, actor, membership_id

    async def update(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        listing_id: UUID,
        request: ListingUpdateRequest,
    ) -> ListingPrivateResponse:
        async with session.begin():
            listing = await self._repository.get(session, listing_id, for_update=True)
            if listing is None:
                raise self.listing_not_found()
            actor, membership_id = await self._authorize(
                session, actor_user_id=actor_user_id, listing=listing
            )
            if listing.lifecycle_status != "draft":
                raise AppError(
                    status=409, code="LISTING_NOT_EDITABLE", title="Listing is not editable"
                )
            if listing.version != request.expected_version:
                raise AppError(
                    status=409, code="LISTING_VERSION_CONFLICT", title="Listing state changed"
                )
            self._validate_specific_spec(listing, request)
            changed = self._apply_scalar_updates(listing, request)
            if (
                request.vehicle_spec is not None
                or request.car_spec is not None
                or request.bike_spec is not None
            ):
                await self._repository.replace_specs(
                    session,
                    listing=listing,
                    vehicle_spec=VehicleSpec(
                        listing_id=listing.id, **request.vehicle_spec.model_dump()
                    )
                    if request.vehicle_spec
                    else None,
                    car_spec=CarSpec(listing_id=listing.id, **request.car_spec.model_dump())
                    if request.car_spec
                    else None,
                    bike_spec=BikeSpec(listing_id=listing.id, **request.bike_spec.model_dump())
                    if request.bike_spec
                    else None,
                )
                changed.append("specifications")
            if changed:
                listing.version += 1
                self._audit.record(
                    session,
                    action="listing.draft.updated",
                    outcome="success",
                    resource_type="listing",
                    actor_user_id=actor.id,
                    organization_id=listing.owner_organization_id,
                    membership_id=membership_id,
                    resource_id=listing.id,
                    changes={"changed_fields": ",".join(changed), "version": listing.version},
                    request_id=get_request_id(),
                )
                enqueue_event(
                    session,
                    event_type="listing.draft.updated",
                    aggregate_type="listing",
                    aggregate_id=listing.id,
                    payload={"changed_fields": changed, "version": listing.version},
                )
            await session.flush()
            if changed:
                await session.refresh(listing, attribute_names=["updated_at"])
            response = await self._response(session, listing)
        return response

    async def list_owned(
        self,
        session: AsyncSession,
        *,
        actor_user_id: UUID,
        owner_type: str,
        organization_id: UUID | None,
        lifecycle_status: str,
        cursor: str | None,
        limit: int,
    ) -> ListingPageResponse:
        actor = await self._identities.get_user_by_id(session, actor_user_id)
        if actor is None or actor.status != "active":
            raise self.listing_not_found()
        owner_user_id: UUID | None = actor.id
        owner_organization_id: UUID | None = None
        if owner_type == "dealer_organization":
            if organization_id is None:
                raise AppError(
                    status=422,
                    code="OWNER_CONTEXT_INVALID",
                    title="Dealer organization context is required",
                )
            await self._authorize_organization_inventory(
                session, actor=actor, organization_id=organization_id
            )
            owner_user_id = None
            owner_organization_id = organization_id
        elif organization_id is not None:
            raise AppError(
                status=422,
                code="OWNER_CONTEXT_INVALID",
                title="Organization context is not valid for personal listings",
            )
        filter_key = f"{actor.id}:{owner_type}:{organization_id}:{lifecycle_status}"
        decoded = self._cursors.decode(cursor, filter_key=filter_key) if cursor else None
        rows = await self._repository.list_owned(
            session,
            owner_user_id=owner_user_id,
            owner_organization_id=owner_organization_id,
            lifecycle_status=lifecycle_status,
            before_updated_at=decoded.updated_at if decoded else None,
            before_id=decoded.listing_id if decoded else None,
            limit=limit + 1,
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        items = [await self._response(session, row) for row in page_rows]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = self._cursors.encode(
                ListingCursor(updated_at=last.updated_at, listing_id=last.id),
                filter_key=filter_key,
            )
        return ListingPageResponse(items=items, next_cursor=next_cursor)

    async def _authorize_organization_inventory(
        self, session: AsyncSession, *, actor: User, organization_id: UUID
    ) -> UUID:
        organization = await self._dealers.get_organization(session, organization_id)
        membership = await self._dealers.get_membership_for_user(
            session, organization_id=organization_id, user_id=actor.id
        )
        if organization is None or membership is None:
            raise self.listing_not_found()
        context = AuthorizationContext(
            user_id=actor.id,
            user_status=actor.status,
            email_verified=actor.email_verified_at is not None,
            phone_verified=actor.phone_verified_at is not None,
            organization=OrganizationAccess(
                organization_id=organization.id,
                organization_status=organization.status,
                organization_verification_status=organization.verification_status,
                membership_id=membership.id,
                membership_status=membership.status,
                membership_role=membership.role,
            ),
        )
        if not self._policy.permits(context, DealerPermission.INVENTORY_MANAGE):
            raise self.listing_not_found()
        return membership.id

    async def _creation_owner(
        self, session: AsyncSession, *, actor: User, request: ListingCreateRequest
    ) -> tuple[str, UUID | None, UUID | None, UUID | None]:
        base = AuthorizationContext(
            user_id=actor.id,
            user_status=actor.status,
            email_verified=actor.email_verified_at is not None,
            phone_verified=actor.phone_verified_at is not None,
        )
        if not isinstance(request.owner_context, DealerOwnerContext):
            if not self._policy.can_create_private_draft(base):
                raise AppError(
                    status=403,
                    code="DRAFT_CREATION_NOT_PERMITTED",
                    title="Account is not permitted to create a private draft",
                )
            return "user", actor.id, None, None
        organization = await self._dealers.get_organization(
            session, request.owner_context.organization_id
        )
        membership = await self._dealers.get_membership_for_user(
            session, organization_id=request.owner_context.organization_id, user_id=actor.id
        )
        if organization is None or membership is None:
            raise self.listing_not_found()
        context = AuthorizationContext(
            user_id=actor.id,
            user_status=actor.status,
            email_verified=base.email_verified,
            phone_verified=base.phone_verified,
            organization=OrganizationAccess(
                organization_id=organization.id,
                organization_status=organization.status,
                organization_verification_status=organization.verification_status,
                membership_id=membership.id,
                membership_status=membership.status,
                membership_role=membership.role,
            ),
        )
        if not self._policy.permits(context, DealerPermission.INVENTORY_MANAGE):
            raise self.listing_not_found()
        return "dealer_organization", None, organization.id, membership.id

    async def _authorize(
        self, session: AsyncSession, *, actor_user_id: UUID, listing: Listing
    ) -> tuple[User, UUID | None]:
        actor = await self._identities.get_user_by_id(session, actor_user_id)
        if actor is None or actor.status != "active":
            raise self.listing_not_found()
        if listing.owner_user_id is not None:
            if listing.owner_user_id != actor.id or listing.owner_organization_id is not None:
                raise self.listing_not_found()
            return actor, None
        if listing.owner_organization_id is None:
            raise self.listing_not_found()
        organization = await self._dealers.get_organization(session, listing.owner_organization_id)
        membership = await self._dealers.get_membership_for_user(
            session, organization_id=listing.owner_organization_id, user_id=actor.id
        )
        if organization is None or membership is None:
            raise self.listing_not_found()
        context = AuthorizationContext(
            user_id=actor.id,
            user_status=actor.status,
            email_verified=actor.email_verified_at is not None,
            phone_verified=actor.phone_verified_at is not None,
            organization=OrganizationAccess(
                organization_id=organization.id,
                organization_status=organization.status,
                organization_verification_status=organization.verification_status,
                membership_id=membership.id,
                membership_status=membership.status,
                membership_role=membership.role,
            ),
        )
        if not self._policy.can_manage_owned_resource(
            context,
            owner_user_id=None,
            owner_organization_id=listing.owner_organization_id,
            dealer_permission=DealerPermission.INVENTORY_MANAGE,
        ):
            raise self.listing_not_found()
        return actor, membership.id

    @staticmethod
    def _validate_specific_spec(listing: Listing, request: ListingUpdateRequest) -> None:
        if (request.car_spec is not None and listing.vehicle_type != "car") or (
            request.bike_spec is not None and listing.vehicle_type != "bike"
        ):
            raise AppError(
                status=422,
                code="VEHICLE_SPEC_TYPE_MISMATCH",
                title="Specification does not match the listing vehicle type",
            )

    @staticmethod
    def _apply_scalar_updates(listing: Listing, request: ListingUpdateRequest) -> list[str]:
        changed: list[str] = []
        for field in ("title", "description", "asking_price"):
            if field in request.model_fields_set and getattr(listing, field) != getattr(
                request, field
            ):
                value = getattr(request, field)
                if field == "asking_price" and value is not None:
                    value = Decimal(str(value))
                setattr(listing, field, value)
                changed.append(field)
        return changed

    async def _response(self, session: AsyncSession, listing: Listing) -> ListingPrivateResponse:
        vehicle, car, bike = await self._repository.get_specs(session, listing.id)
        return ListingPrivateResponse(
            id=listing.id,
            owner_type=listing.owner_type,
            owner_user_id=listing.owner_user_id,
            owner_organization_id=listing.owner_organization_id,
            created_by_user_id=listing.created_by_user_id,
            vehicle_type=listing.vehicle_type,
            variant_id=listing.variant_id,
            lifecycle_status=listing.lifecycle_status,
            publication_status=listing.publication_status,
            moderation_status=listing.moderation_status,
            submitted_listing_version=listing.submitted_listing_version,
            submitted_at=listing.submitted_at,
            title=listing.title,
            description=listing.description,
            asking_price=float(listing.asking_price) if listing.asking_price is not None else None,
            currency=listing.currency,
            version=listing.version,
            vehicle_spec=vehicle,
            car_spec=car,
            bike_spec=bike,
            created_at=listing.created_at,
            updated_at=listing.updated_at,
        )

    @staticmethod
    def listing_not_found() -> AppError:
        return AppError(status=404, code="LISTING_NOT_FOUND", title="Listing not found")
