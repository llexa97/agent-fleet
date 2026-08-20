import hashlib
import json
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, Query, Response, status
from sqlalchemy import select

from apps.api.agent_fleet_api.dependencies import CurrentPrincipal, Database, MutatingPrincipal
from apps.api.agent_fleet_api.models_collaboration import Message, Thread
from apps.api.agent_fleet_api.models_governance import IdempotencyRecord
from apps.api.agent_fleet_api.schemas import ThreadCreate, ThreadResponse, ThreadUpdate
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from apps.api.agent_fleet_api.services.catalog_service import require_channel_membership
from packages.shared.errors import ConflictError, NotFoundError

router = APIRouter(tags=["threads"])


def _body_hash(body: ThreadCreate) -> str:
    raw = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def _thread_for_member(
    db: Database,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    thread_id: UUID,
) -> Thread:
    thread = await db.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.tenant_id == tenant_id)
    )
    if thread is None:
        raise NotFoundError("thread", thread_id)
    await require_channel_membership(
        db,
        tenant_id=tenant_id,
        channel_id=thread.channel_id,
        actor_id=actor_id,
    )
    return thread


@router.get("/channels/{channel_id}/threads", response_model=list[ThreadResponse])
async def list_threads_route(
    channel_id: UUID,
    db: Database,
    principal: CurrentPrincipal,
    include_closed: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=200),
) -> list[Thread]:
    await require_channel_membership(
        db,
        tenant_id=principal.tenant_id,
        channel_id=channel_id,
        actor_id=principal.actor_id,
    )
    statement = select(Thread).where(
        Thread.tenant_id == principal.tenant_id,
        Thread.channel_id == channel_id,
    )
    if not include_closed:
        statement = statement.where(Thread.is_closed.is_(False))
    return list((await db.scalars(statement.order_by(Thread.updated_at.desc()).limit(limit))).all())


@router.post(
    "/channels/{channel_id}/threads",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_thread_route(
    channel_id: UUID,
    body: ThreadCreate,
    response: Response,
    db: Database,
    principal: MutatingPrincipal,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=255),
) -> Thread:
    channel, _membership = await require_channel_membership(
        db,
        tenant_id=principal.tenant_id,
        channel_id=channel_id,
        actor_id=principal.actor_id,
    )
    scope = f"thread:{channel_id}"
    request_hash = _body_hash(body)
    record = await db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == principal.tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.idempotency_key == idempotency_key,
        )
    )
    if record is not None:
        if record.request_hash != request_hash:
            raise ConflictError(
                "idempotency_key_reused",
                "Cette clé d'idempotence correspond à une autre requête",
            )
        if record.resource_id is None:
            raise ConflictError("idempotency_in_progress", "Création de thread en cours")
        existing = await db.get(Thread, record.resource_id)
        if existing is None or existing.tenant_id != principal.tenant_id:
            raise ConflictError("idempotency_corrupt", "Thread idempotent introuvable")
        response.status_code = status.HTTP_200_OK
        return existing
    root: Message | None = None
    if body.root_message_id is not None:
        root = await db.scalar(
            select(Message).where(
                Message.id == body.root_message_id,
                Message.tenant_id == principal.tenant_id,
                Message.channel_id == channel_id,
            )
        )
        if root is None:
            raise NotFoundError("message", body.root_message_id)
        if root.thread_id is not None:
            raise ConflictError(
                "message_already_threaded", "Ce message appartient déjà à un thread"
            )
    record = IdempotencyRecord(
        tenant_id=principal.tenant_id,
        scope=scope,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        resource_type="thread",
    )
    db.add(record)
    thread = Thread(
        tenant_id=principal.tenant_id,
        space_id=channel.space_id,
        channel_id=channel.id,
        title=body.title,
        root_message_id=body.root_message_id,
        created_by_actor_id=principal.actor_id,
    )
    db.add(thread)
    await db.flush()
    record.resource_id = thread.id
    record.response_status = status.HTTP_201_CREATED
    if root is not None:
        root.thread_id = thread.id
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="thread.created",
        resource_type="thread",
        resource_id=thread.id,
    )
    add_internal_event(
        db,
        event_type="thread.created",
        tenant_id=principal.tenant_id,
        space_id=thread.space_id,
        channel_id=thread.channel_id,
        actor_type="human",
        actor_id=principal.actor_id,
        idempotency_key=f"thread.created:{thread.id}",
        payload={"thread_id": str(thread.id)},
    )
    await db.commit()
    return thread


@router.get("/threads/{thread_id}", response_model=ThreadResponse)
async def get_thread_route(
    thread_id: UUID,
    db: Database,
    principal: CurrentPrincipal,
) -> Thread:
    return await _thread_for_member(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        thread_id=thread_id,
    )


@router.patch("/threads/{thread_id}", response_model=ThreadResponse)
async def update_thread_route(
    thread_id: UUID,
    body: ThreadUpdate,
    db: Database,
    principal: MutatingPrincipal,
) -> Thread:
    thread = await _thread_for_member(
        db,
        tenant_id=principal.tenant_id,
        actor_id=principal.actor_id,
        thread_id=thread_id,
    )
    changes = body.model_dump(exclude_unset=True)
    for name, value in changes.items():
        setattr(thread, name, value)
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="thread.updated",
        resource_type="thread",
        resource_id=thread.id,
        details={"fields": sorted(changes)},
    )
    add_internal_event(
        db,
        event_type="thread.updated",
        tenant_id=principal.tenant_id,
        space_id=thread.space_id,
        channel_id=thread.channel_id,
        actor_type="human",
        actor_id=principal.actor_id,
        idempotency_key=f"thread.updated:{thread.id}:{uuid4()}",
        payload={"thread_id": str(thread.id), "fields": sorted(changes)},
    )
    await db.commit()
    return thread
