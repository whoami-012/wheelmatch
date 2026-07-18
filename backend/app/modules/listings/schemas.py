from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PersonalOwnerContext(BaseModel):
    type: Literal["personal"]


class DealerOwnerContext(BaseModel):
    type: Literal["dealer_organization"]
    organization_id: UUID


OwnerContext = Annotated[
    PersonalOwnerContext | DealerOwnerContext,
    Field(discriminator="type"),
]


class ListingCreateRequest(BaseModel):
    owner_context: OwnerContext
    vehicle_type: Literal["car", "bike"]
    variant_id: UUID | None = None


class VehicleSpecInput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    manufacture_year: int = Field(ge=1886, le=2100)
    odometer_km: int = Field(ge=0, le=10_000_000)
    fuel_type: Literal["petrol", "diesel", "electric", "hybrid", "cng", "lpg", "other"]
    transmission: Literal["manual", "automatic", "cvt", "single_speed", "other"]
    ownership_count: int = Field(ge=1, le=20)
    colour: str = Field(min_length=1, max_length=40)
    condition: Literal["excellent", "good", "fair", "project"]


class CarSpecInput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    body_type: str = Field(min_length=1, max_length=32)
    seats: int = Field(ge=1, le=20)
    engine_cc: int | None = Field(default=None, ge=1, le=20_000)
    drivetrain: str = Field(min_length=1, max_length=16)
    emission_standard: str | None = Field(default=None, max_length=24)


class BikeSpecInput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    bike_category: str = Field(min_length=1, max_length=32)
    engine_cc: int | None = Field(default=None, ge=1, le=5_000)
    start_type: str = Field(min_length=1, max_length=24)
    braking_system: str = Field(min_length=1, max_length=24)


class ListingUpdateRequest(BaseModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=160)
    description: str | None = Field(default=None, max_length=5_000)
    asking_price: float | None = Field(default=None, ge=0, le=1_000_000_000)
    vehicle_spec: VehicleSpecInput | None = None
    car_spec: CarSpecInput | None = None
    bike_spec: BikeSpecInput | None = None

    @model_validator(mode="after")
    def reject_both_specific_specs(self) -> ListingUpdateRequest:
        if self.car_spec is not None and self.bike_spec is not None:
            raise ValueError("car_spec and bike_spec are mutually exclusive")
        return self


class ListingPrivateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_type: Literal["user", "dealer_organization"]
    owner_user_id: UUID | None
    owner_organization_id: UUID | None
    created_by_user_id: UUID
    vehicle_type: Literal["car", "bike"]
    variant_id: UUID | None
    lifecycle_status: Literal["draft", "removed"]
    publication_status: Literal["private", "pending"]
    moderation_status: Literal["not_started", "pending"]
    submitted_listing_version: int | None
    submitted_at: datetime | None
    title: str | None
    description: str | None
    asking_price: float | None
    currency: str
    version: int
    vehicle_spec: VehicleSpecInput | None = None
    car_spec: CarSpecInput | None = None
    bike_spec: BikeSpecInput | None = None
    created_at: datetime
    updated_at: datetime


class ListingPageResponse(BaseModel):
    items: list[ListingPrivateResponse]
    next_cursor: str | None = None
