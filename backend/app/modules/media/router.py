from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.errors.models import ProblemDetail
from app.core.idempotency import canonical_request_hash
from app.modules.identity.dependencies import get_current_principal
from app.modules.identity.session_service import CurrentPrincipal
from app.modules.media.dependencies import get_media_service
from app.modules.media.schemas import (
    MediaCompleteRequest,
    MediaStatusResponse,
    MediaUploadIntentRequest,
    MediaUploadIntentResponse,
)
from app.modules.media.service import MediaService

router = APIRouter(prefix="/api/v1/media", tags=["media"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PrincipalDependency = Annotated[CurrentPrincipal, Depends(get_current_principal)]
ServiceDependency = Annotated[MediaService, Depends(get_media_service)]
IdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"),
]
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ProblemDetail},
    404: {"model": ProblemDetail},
    409: {"model": ProblemDetail},
    422: {"model": ProblemDetail},
    503: {"model": ProblemDetail},
}


@router.post(
    "/upload-intents",
    response_model=MediaUploadIntentResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
async def create_upload_intent(
    body: MediaUploadIntentRequest,
    idempotency_key: IdempotencyKey,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> MediaUploadIntentResponse:
    return await service.create_intent(
        session,
        actor_user_id=principal.user_id,
        request=body,
        idempotency_key=idempotency_key,
        request_hash=canonical_request_hash(
            method="POST", path="/api/v1/media/upload-intents", payload=body.model_dump(mode="json")
        ),
    )


@router.post("/{media_id}/complete", response_model=MediaStatusResponse, responses=_ERRORS)
async def complete_upload(
    media_id: UUID,
    body: MediaCompleteRequest,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> MediaStatusResponse:
    return await service.complete(
        session, actor_user_id=principal.user_id, media_id=media_id, request=body
    )


@router.get("/{media_id}/status", response_model=MediaStatusResponse, responses=_ERRORS)
async def get_media_status(
    media_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> MediaStatusResponse:
    return await service.status(session, actor_user_id=principal.user_id, media_id=media_id)


@router.delete("/{media_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS)
async def remove_media(
    media_id: UUID,
    principal: PrincipalDependency,
    session: SessionDependency,
    service: ServiceDependency,
) -> Response:
    await service.remove(session, actor_user_id=principal.user_id, media_id=media_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
