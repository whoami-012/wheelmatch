from __future__ import annotations

from typing import Any, cast

import pytest

from app.core.config.secrets import AwsSecretsManagerProvider, EnvironmentSecretProvider
from app.core.telemetry.sentry import _before_send


class FakeSecretsClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.requested_name: str | None = None

    def get_secret_value(self, *, SecretId: str) -> dict[str, Any]:
        self.requested_name = SecretId
        return self.response


def test_environment_secret_provider_accepts_only_string_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_SECRET_BUNDLE", '{"DATABASE_PASSWORD":"private"}')

    bundle = EnvironmentSecretProvider().get_secret_bundle("TEST_SECRET_BUNDLE")

    assert bundle == {"DATABASE_PASSWORD": "private"}


@pytest.mark.parametrize("raw", ["[]", '{"count":1}', "not-json"])
def test_environment_secret_provider_rejects_invalid_bundles(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("TEST_SECRET_BUNDLE", raw)

    with pytest.raises((ValueError, TypeError)):
        EnvironmentSecretProvider().get_secret_bundle("TEST_SECRET_BUNDLE")


def test_aws_secret_provider_returns_parsed_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeSecretsClient({"SecretString": '{"TOKEN":"private"}'})
    monkeypatch.setattr("app.core.config.secrets.boto3.client", lambda *args, **kwargs: client)

    provider = AwsSecretsManagerProvider(region_name="us-east-1")

    assert provider.get_secret_bundle("wheelmatch/test") == {"TOKEN": "private"}
    assert client.requested_name == "wheelmatch/test"


def test_sentry_redacts_sensitive_request_content() -> None:
    event: Any = {
        "request": {
            "headers": {"Authorization": "private", "Accept": "application/json"},
            "cookies": "session=private",
            "data": {"password": "private"},
            "query_string": "token=private",
        }
    }

    redacted = cast(Any, _before_send(event, {}))

    assert redacted["request"]["headers"]["Authorization"] == "[Filtered]"
    assert redacted["request"]["headers"]["Accept"] == "application/json"
    assert "cookies" not in redacted["request"]
    assert "data" not in redacted["request"]
    assert "query_string" not in redacted["request"]
