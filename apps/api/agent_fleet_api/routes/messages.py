from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status

from apps.api.agent_fleet_api.dependencies import CurrentPrincipal, Database, MutatingPrincipal
from apps.api.agent_fleet_api.schemas import MessageCreate, MessageResponse
from apps.api.agent_fleet_api.services.message_service import (
    list_channel_messages,
    post_message,
    serialize_message,
)

router = APIRouter(prefix="/channels/{channel_id}/messages", tags=["messages"])


@router.get("", response_model=list[MessageResponse])
async def list_messages_route(
    channel_id: UUID,
    db: Database,
    principal: CurrentPrincipal,
    thread_id: UUID | None = Query(default=None),
    before: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, object]]:
    messages = await list_channel_messages(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        channel_id=channel_id,
        thread_id=thread_id,
        before=before,
        limit=limit,
    )
    return [await serialize_message(db, item) for item in messages]


@router.post("", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def post_message_route(
    channel_id: UUID,
    body: MessageCreate,
    response: Response,
    db: Database,
    principal: MutatingPrincipal,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> dict[str, object]:
    result = await post_message(
        db,
        tenant_id=principal.tenant_id,
        author_actor_id=principal.actor_id,
        author_type="human",
        channel_id=channel_id,
        data=body,
        idempotency_key=idempotency_key,
    )
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return await serialize_message(db, result.message)
