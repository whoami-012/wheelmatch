from __future__ import annotations

from fastapi.testclient import TestClient

from app.bootstrap import create_app
from app.core.config import Environment, Settings
from app.core.health import HealthService


def test_phase1_public_auth_openapi_contracts_are_versioned_and_secret_safe() -> None:
    app = create_app(
        settings=Settings(environment=Environment.TEST, log_level="CRITICAL"),
        health_service=HealthService(probes={}, timeout_seconds=0.1),
    )
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    paths = schema["paths"]
    assert "/api/v1/auth/register" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/refresh" in paths
    assert "/api/v1/auth/logout" in paths
    assert "/api/v1/auth/logout-all" in paths
    assert "/api/v1/auth/password/change" in paths
    assert "/api/v1/auth/verify-email" in paths
    assert "/api/v1/auth/verify-phone" in paths
    assert "/api/v1/auth/recovery/request" in paths
    assert "/api/v1/auth/recovery/reset" in paths
    assert "/api/v1/me/profile" in paths
    assert "/api/v1/me/capabilities" in paths
    assert "/api/v1/me/sessions" in paths
    assert "/api/v1/dealer-organizations" in paths
    registration = schema["components"]["schemas"]["RegisterRequest"]
    assert "account_type" not in registration["properties"]
    response = schema["components"]["schemas"]["RegistrationResponse"]
    serialized = str(response).casefold()
    assert "password_hash" not in serialized
    assert "secret_hash" not in serialized
    assert "token_hash" not in serialized
