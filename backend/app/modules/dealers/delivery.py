from __future__ import annotations

from typing import Protocol
from uuid import UUID


class DealerInvitationDelivery(Protocol):
    async def send_invitation(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        membership_id: UUID,
        token: str,
    ) -> None: ...


class NullDealerInvitationDelivery:
    async def send_invitation(
        self,
        *,
        user_id: UUID,
        organization_id: UUID,
        membership_id: UUID,
        token: str,
    ) -> None:
        del user_id, organization_id, membership_id, token
