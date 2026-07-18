from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class ListingLocationWriteRequest(BaseModel):
    expected_version: int = Field(ge=1)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    locality: str = Field(min_length=1, max_length=120)
    coarse_area: str = Field(min_length=1, max_length=120)
    visibility: Literal["approximate", "public_business"] = "approximate"
    public_address_id: UUID | None = None

    @model_validator(mode="after")
    def validate_location_source(self) -> ListingLocationWriteRequest:
        if self.visibility == "approximate":
            if (
                self.latitude is None
                or self.longitude is None
                or self.public_address_id is not None
            ):
                raise ValueError("approximate location requires coordinates and no public address")
        elif (
            self.public_address_id is None
            or self.latitude is not None
            or self.longitude is not None
        ):
            raise ValueError("public business location requires only a public address")
        return self


class ListingLocationProjection(BaseModel):
    locality: str
    coarse_area: str
    distance_band: str | None = None
    public_business_address: str | None = None
    visibility: Literal["approximate", "public_business"]
    listing_version: int
