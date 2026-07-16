from __future__ import annotations

from fastapi.testclient import TestClient

from app.bootstrap import create_app
from app.core.config import Environment, Settings
from app.core.health import HealthService


class PassingProbe:
    async def check(self) -> None:
        return None


class FailingProbe:
    async def check(self) -> None:
        raise RuntimeError("synthetic failure")


def build_client(*, failing: bool = False) -> TestClient:
    probe = FailingProbe() if failing else PassingProbe()
    app = create_app(
        settings=Settings(environment=Environment.TEST, log_level="CRITICAL"),
        health_service=HealthService(probes={"synthetic": probe}, timeout_seconds=0.1),
    )
    return TestClient(app, raise_server_exceptions=False)


def test_liveness_has_request_id_and_security_headers() -> None:
    with build_client() as client:
        response = client.get("/health/live", headers={"x-request-id": "request-1234"})

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"] == "request-1234"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_readiness_returns_problem_details_when_dependency_fails() -> None:
    with build_client(failing=True) as client:
        response = client.get("/health/ready")

    body = response.json()
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert body["code"] == "SERVICE_NOT_READY"
    assert body["meta"] == {"checks": {"synthetic": "unavailable"}}
    assert body["correlation_id"] == response.headers["x-request-id"]


def test_unknown_route_uses_problem_contract() -> None:
    with build_client() as client:
        response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["code"] == "HTTP_ERROR"
