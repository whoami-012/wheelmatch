from __future__ import annotations

from fastapi.testclient import TestClient

from app.bootstrap import create_app
from app.core.config import Environment, Settings
from app.core.health import HealthService


def test_phase2_openapi_contracts_are_private_and_bounded() -> None:
    app = create_app(
        settings=Settings(environment=Environment.TEST, log_level="CRITICAL"),
        health_service=HealthService(probes={}, timeout_seconds=0.1),
    )
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    paths = schema["paths"]
    for path in (
        "/api/v1/catalogue/search",
        "/api/v1/listings",
        "/api/v1/listings/{listing_id}",
        "/api/v1/listings/{listing_id}/location",
        "/api/v1/media/upload-intents",
        "/api/v1/media/{media_id}/complete",
        "/api/v1/media/{media_id}/status",
        "/api/v1/me/listings",
    ):
        assert path in paths

    location = schema["components"]["schemas"]["ListingLocationProjection"]
    serialized = str(location).casefold()
    for forbidden in (
        "latitude",
        "longitude",
        "exact_point",
        "street_address",
        "geohash",
        "coarse_cell",
        "exact_distance",
    ):
        assert forbidden not in serialized
