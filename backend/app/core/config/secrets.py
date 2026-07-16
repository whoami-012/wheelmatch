from __future__ import annotations

import json
import os
from typing import Protocol

import boto3


class SecretProvider(Protocol):
    def get_secret_bundle(self, name: str) -> dict[str, str]: ...


class EnvironmentSecretProvider:
    """Resolve a JSON secret bundle from an environment variable."""

    def get_secret_bundle(self, name: str) -> dict[str, str]:
        raw = os.environ.get(name)
        if raw is None:
            raise KeyError(f"secret bundle is not configured: {name}")
        return _parse_secret_bundle(raw)


class AwsSecretsManagerProvider:
    def __init__(self, *, region_name: str, endpoint_url: str | None = None) -> None:
        self._client = boto3.client(
            "secretsmanager",
            region_name=region_name,
            endpoint_url=endpoint_url,
        )

    def get_secret_bundle(self, name: str) -> dict[str, str]:
        response = self._client.get_secret_value(SecretId=name)
        secret_string = response.get("SecretString")
        if not isinstance(secret_string, str):
            raise ValueError("binary secret bundles are not supported")
        return _parse_secret_bundle(secret_string)


def _parse_secret_bundle(raw: str) -> dict[str, str]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in parsed.items()
    ):
        raise ValueError("secret bundle must be a JSON object of string values")
    return parsed
