from __future__ import annotations

import re
import unicodedata
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalogue.repository import CatalogueRepository
from app.modules.catalogue.schemas import CatalogueItem, CataloguePage, CatalogueSearchPage

_SPACES = re.compile(r"\s+")


def normalize_catalogue_name(value: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", value).casefold()).strip()


class CatalogueService:
    def __init__(self, repository: CatalogueRepository) -> None:
        self._repository = repository

    async def list_makes(
        self, session: AsyncSession, *, vehicle_type: str, query: str | None, limit: int
    ) -> CataloguePage:
        normalized = normalize_catalogue_name(query) if query else None
        rows = await self._repository.list_makes(
            session, vehicle_type=vehicle_type, normalized_query=normalized, limit=limit
        )
        return CataloguePage(items=[CatalogueItem.model_validate(row) for row in rows], limit=limit)

    async def list_models(
        self, session: AsyncSession, *, make_id: UUID, vehicle_type: str, limit: int
    ) -> CataloguePage:
        rows = await self._repository.list_models(
            session, make_id=make_id, vehicle_type=vehicle_type, limit=limit
        )
        return CataloguePage(items=[CatalogueItem.model_validate(row) for row in rows], limit=limit)

    async def list_variants(
        self, session: AsyncSession, *, model_id: UUID, limit: int
    ) -> CataloguePage:
        rows = await self._repository.list_variants(session, model_id=model_id, limit=limit)
        return CataloguePage(items=[CatalogueItem.model_validate(row) for row in rows], limit=limit)

    async def search(
        self, session: AsyncSession, *, vehicle_type: str, query: str, limit: int
    ) -> CatalogueSearchPage:
        rows = await self._repository.search(
            session,
            vehicle_type=vehicle_type,
            normalized_query=normalize_catalogue_name(query),
            limit=limit,
        )
        return CatalogueSearchPage(items=rows, limit=limit)
