from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
from jwt import InvalidTokenError

from app.core.ids import uuid7


class AccessTokenError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    user_id: UUID
    session_family_id: UUID
    token_id: UUID
    issued_at: datetime
    expires_at: datetime


class AccessTokenService:
    """Issue and validate identity-only HS256 access tokens for the modular monolith."""

    def __init__(
        self,
        *,
        signing_key: str,
        issuer: str,
        audience: str,
        ttl_seconds: int,
    ) -> None:
        if len(signing_key.encode("utf-8")) < 32:
            raise ValueError("access token signing key must be at least 32 bytes")
        self._signing_key = signing_key
        self._issuer = issuer
        self._audience = audience
        self._ttl = timedelta(seconds=ttl_seconds)

    @property
    def ttl_seconds(self) -> int:
        return int(self._ttl.total_seconds())

    def issue(
        self,
        *,
        user_id: UUID,
        session_family_id: UUID,
        now: datetime | None = None,
    ) -> str:
        issued_at = (now or datetime.now(UTC)).astimezone(UTC)
        expires_at = issued_at + self._ttl
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(user_id),
            "sid": str(session_family_id),
            "jti": str(uuid7()),
            "iat": issued_at,
            "nbf": issued_at,
            "exp": expires_at,
            "token_use": "access",
        }
        return jwt.encode(payload, self._signing_key, algorithm="HS256", headers={"typ": "JWT"})

    def decode(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._signing_key,
                algorithms=["HS256"],
                issuer=self._issuer,
                audience=self._audience,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "sid",
                        "jti",
                        "iat",
                        "nbf",
                        "exp",
                        "token_use",
                    ]
                },
            )
            if payload.get("token_use") != "access":
                raise AccessTokenError("wrong token use")
            return AccessTokenClaims(
                user_id=UUID(str(payload["sub"])),
                session_family_id=UUID(str(payload["sid"])),
                token_id=UUID(str(payload["jti"])),
                issued_at=datetime.fromtimestamp(int(payload["iat"]), tz=UTC),
                expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
            )
        except (InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise AccessTokenError("invalid access token") from exc
