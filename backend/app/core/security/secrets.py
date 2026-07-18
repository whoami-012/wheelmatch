from __future__ import annotations

import hashlib
import hmac
import secrets


class SecretHasher:
    def __init__(self, key: str) -> None:
        if len(key.encode("utf-8")) < 32:
            raise ValueError("secret hash key must be at least 32 bytes")
        self._key = key.encode("utf-8")

    def digest(self, value: str) -> str:
        return hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify(self, value: str, expected_digest: str) -> bool:
        return hmac.compare_digest(self.digest(value), expected_digest)


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def generate_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"
