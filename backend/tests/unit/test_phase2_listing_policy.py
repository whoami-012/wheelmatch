from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.modules.listings.schemas import BikeSpecInput, CarSpecInput, ListingUpdateRequest


def test_listing_update_rejects_multiple_vehicle_specific_shapes() -> None:
    with pytest.raises(ValidationError):
        ListingUpdateRequest(
            expected_version=1,
            car_spec=CarSpecInput(body_type="sedan", seats=5, drivetrain="fwd"),
            bike_spec=BikeSpecInput(
                bike_category="standard",
                start_type="electric",
                braking_system="abs",
            ),
        )
