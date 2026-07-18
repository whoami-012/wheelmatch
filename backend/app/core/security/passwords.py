from __future__ import annotations

import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

_COMMON_PASSWORDS = frozenset(
    {
        "123456789012",
        "letmeinplease",
        "password1234",
        "qwertyuiop12",
        "wheelmatch123",
    }
)


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    minimum_length: int = 12
    maximum_length: int = 128

    def validate(self, password: str, *, normalized_email: str | None = None) -> None:
        if len(password) < self.minimum_length:
            raise ValueError(f"password must contain at least {self.minimum_length} characters")
        if len(password) > self.maximum_length:
            raise ValueError(f"password must contain no more than {self.maximum_length} characters")
        normalized = password.casefold()
        if normalized in _COMMON_PASSWORDS:
            raise ValueError("password is too common")
        if normalized_email is not None:
            local_part = normalized_email.partition("@")[0]
            if len(local_part) >= 4 and local_part in normalized:
                raise ValueError("password must not contain the email name")


class PasswordService:
    """Argon2id password operations with bounded, explicit server-side parameters."""

    def __init__(self, *, policy: PasswordPolicy | None = None) -> None:
        self.policy = policy or PasswordPolicy()
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(32))

    def hash(self, password: str, *, normalized_email: str | None = None) -> str:
        self.policy.validate(password, normalized_email=normalized_email)
        return self._hasher.hash(password)

    def rehash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, stored_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(stored_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    def verify_or_dummy(self, stored_hash: str | None, password: str) -> bool:
        verified = self.verify(stored_hash or self._dummy_hash, password)
        return verified if stored_hash is not None else False

    def needs_rehash(self, stored_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except InvalidHashError:
            return True
