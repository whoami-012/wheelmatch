from __future__ import annotations

from typing import Protocol
from uuid import UUID


class IdentityChallengeDelivery(Protocol):
    async def send_verification(
        self,
        *,
        user_id: UUID,
        kind: str,
        destination: str,
        challenge_id: UUID,
        code: str,
    ) -> None: ...

    async def send_recovery(
        self,
        *,
        user_id: UUID,
        destination: str,
        challenge_id: UUID,
        token: str,
    ) -> None: ...


class NullIdentityChallengeDelivery:
    """Provider boundary used until an approved delivery adapter is configured."""

    async def send_verification(
        self,
        *,
        user_id: UUID,
        kind: str,
        destination: str,
        challenge_id: UUID,
        code: str,
    ) -> None:
        del user_id, kind, destination, challenge_id, code

    async def send_recovery(
        self,
        *,
        user_id: UUID,
        destination: str,
        challenge_id: UUID,
        token: str,
    ) -> None:
        del user_id, destination, challenge_id, token
