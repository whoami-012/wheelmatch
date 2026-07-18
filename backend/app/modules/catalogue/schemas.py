from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

VehicleType = Literal["car", "bike"]


class CatalogueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str


class CatalogueSearchItem(BaseModel):
    variant_id: UUID
    variant_name: str
    model_id: UUID
    model_name: str
    make_id: UUID
    make_name: str
    vehicle_type: VehicleType


class CataloguePage(BaseModel):
    items: list[CatalogueItem]
    limit: int = Field(ge=1, le=50)


class CatalogueSearchPage(BaseModel):
    items: list[CatalogueSearchItem]
    limit: int = Field(ge=1, le=50)
