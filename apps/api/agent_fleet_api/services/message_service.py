import hashlib
import json
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_collaboration import (
    AgentChannelMembership,
    ChannelMember,
    Message,
    MessageMention,
    Task,
    Thread,
    Trace,
    TraceParticipant,
)
from apps.api.agent_fleet_api.models_execution import Delivery, DeliveryQueue
from apps.api.agent_fleet_api.models_governance import IdempotencyRecord
from apps.api.agent_fleet_api.models_identity import Actor, Agent
from apps.api.agent_fleet_api.schemas import MessageCreate
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from apps.api.agent_fleet_api.services.catalog_service import require_channel_membership
from apps.api.agent_fleet_api.services.orchestration_policy import (
    DEFAULT_TRACE_POLICY,
    check_delivery_allowed,
)
from packages.shared.errors import ConflictError, DomainError, NotFoundError
from packages.shared.time import utcnow

_WHITESPACE = re.compile(r"\s+")


@dataclass(slots=True)
class CreatedMessage:
    message: Message
    mentions: list[MessageMention]
    duplicate: bool = False


def normalized_message_hash(content: str) -> str:
    normalized = _WHITESPACE.sub(" ", content.strip().casefold())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _request_hash(data: MessageCreate, task_id: UUID | None = None) -> str:
    body = data.model_dump(mode="json")
    body["task_id"] = str(task_id) if task_id else None
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


async def _upsert_queue(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    space_id: UUID,
    agent_id: UUID,
    channel_id: UUID,
    queue_key: str,
) -> None:
    now = utcnow()
    values = {
        "id": uuid4(),
        "tenant_id": tenant_id,
        "space_id": space_id,
        "agent_id": agent_id,
        "channel_id": channel_id,
        "queue_key": queue_key,
        "pending_count": 1,
        "next_wake_at": now,
        "created_at": now,
        "updated_at": now,
    }
    dialect = db.bind.dialect.name if db.bind else "unknown"
    if dialect == "postgresql":
        pg_statement = pg_insert(DeliveryQueue).values(**values)
        pg_statement = pg_statement.on_conflict_do_update(
            constraint="uq_delivery_queues_logical",
            set_={
                "pending_count": DeliveryQueue.pending_count + 1,
                "next_wake_at": now,
                "updated_at": now,
            },
        )
        await db.execute(pg_statement)
        return
    if dialect == "sqlite":
        sqlite_statement = sqlite_insert(DeliveryQueue).values(**values)
        sqlite_statement = sqlite_statement.on_conflict_do_update(
            index_elements=["tenant_id", "agent_id", "channel_id"],
            set_={
                "pending_count": DeliveryQueue.pending_count + 1,
                "next_wake_at": now,
                "updated_at": now,
            },
        )
        await db.execute(sqlite_statement)
        return
    queue = await db.scalar(
        select(DeliveryQueue)
        .where(
            DeliveryQueue.tenant_id == tenant_id,
            DeliveryQueue.agent_id == agent_id,
            DeliveryQueue.channel_id == channel_id,
        )
        .with_for_update()
    )
    if queue is None:
        db.add(DeliveryQueue(**values))
    else:
        queue.pending_count += 1
        queue.next_wake_at = now


async def _existing_idempotent_message(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    scope: str,
    key: str,
    request_hash: str,
) -> Message | None:
    record = await db.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.idempotency_key == key,
        )
    )
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise ConflictError(
            "idempotency_key_reused",
            "Cette clé d’idempotence a déjà été utilisée avec un autre contenu",
        )
    if record.resource_id is None:
        raise ConflictError("idempotency_in_progress", "La requête identique est encore en cours")
    message = await db.get(Message, record.resource_id)
    if message is None or message.tenant_id != tenant_id:
        raise ConflictError("idempotency_corrupt", "Résultat idempotent introuvable")
    return message


async def _get_mentions(db: AsyncSession, message_id: UUID) -> list[MessageMention]:
    return list(
        (
            await db.scalars(
                select(MessageMention)
                .where(MessageMention.message_id == message_id)
                .order_by(MessageMention.created_at, MessageMention.id)
            )
        ).all()
    )


async def post_message(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    author_actor_id: UUID,
    author_type: str,
    channel_id: UUID,
    data: MessageCreate,
    idempotency_key: str,
    source_agent_id: UUID | None = None,
    trace_id: UUID | None = None,
    parent_delivery_id: UUID | None = None,
    task_id: UUID | None = None,
    depth: int = 0,
    commit: bool = True,
) -> CreatedMessage:
    if len(idempotency_key) < 8 or len(idempotency_key) > 255:
        raise DomainError(
            "invalid_idempotency_key",
            "Idempotency-Key doit contenir entre 8 et 255 caractères",
            status_code=422,
        )
    channel, _membership = await require_channel_membership(
        db,
        tenant_id=tenant_id,
        channel_id=channel_id,
        actor_id=author_actor_id,
    )
    scope = f"message:{channel_id}"
    body_hash = _request_hash(data, task_id)
    duplicate = await _existing_idempotent_message(
        db,
        tenant_id=tenant_id,
        scope=scope,
        key=idempotency_key,
        request_hash=body_hash,
    )
    if duplicate is not None:
        return CreatedMessage(duplicate, await _get_mentions(db, duplicate.id), duplicate=True)
    record = IdempotencyRecord(
        tenant_id=tenant_id,
        scope=scope,
        idempotency_key=idempotency_key,
        request_hash=body_hash,
        resource_type="message",
    )
    db.add(record)
    await db.flush()

    if data.thread_id is not None:
        thread = await db.scalar(
            select(Thread).where(
                Thread.id == data.thread_id,
                Thread.tenant_id == tenant_id,
                Thread.channel_id == channel_id,
            )
        )
        if thread is None:
            raise NotFoundError("thread", data.thread_id)
    if data.reply_to_id is not None:
        reply = await db.scalar(
            select(Message.id).where(
                Message.id == data.reply_to_id,
                Message.tenant_id == tenant_id,
                Message.channel_id == channel_id,
            )
        )
        if reply is None:
            raise NotFoundError("message", data.reply_to_id)
    if task_id is not None:
        task = await db.scalar(
            select(Task).where(
                Task.id == task_id,
                Task.tenant_id == tenant_id,
                Task.space_id == channel.space_id,
                (Task.channel_id == channel_id) | (Task.channel_id.is_(None)),
            )
        )
        if task is None:
            raise NotFoundError("task", task_id)

    trace: Trace | None = None
    if trace_id is not None:
        trace = await db.scalar(
            select(Trace).where(
                Trace.id == trace_id,
                Trace.tenant_id == tenant_id,
                Trace.space_id == channel.space_id,
            )
        )
        if trace is None:
            raise NotFoundError("trace", trace_id)
    elif any(item.target_type == "agent" for item in data.mentions) or data.expects_response:
        trace = Trace(
            tenant_id=tenant_id,
            space_id=channel.space_id,
            channel_id=channel.id,
            thread_id=data.thread_id,
            initiator_actor_id=author_actor_id,
            status="running",
            policy=dict(DEFAULT_TRACE_POLICY),
        )
        db.add(trace)
        await db.flush()

    message = Message(
        tenant_id=tenant_id,
        space_id=channel.space_id,
        channel_id=channel.id,
        thread_id=data.thread_id,
        author_type=author_type,
        author_id=author_actor_id,
        content=data.content,
        reply_to_id=data.reply_to_id,
        trace_id=trace.id if trace else None,
        task_id=task_id,
        expects_response=data.expects_response or bool(data.mentions),
        idempotency_key=idempotency_key,
    )
    db.add(message)
    await db.flush()
    record.resource_id = message.id
    record.response_status = 201
    if trace is not None and trace.trigger_message_id is None:
        trace.trigger_message_id = message.id

    seen: set[tuple[str, UUID]] = set()
    mentions: list[MessageMention] = []
    correlation_id = uuid4()
    content_hash = normalized_message_hash(data.content)
    blocked_reason: str | None = None
    for input_mention in data.mentions:
        identity = (input_mention.target_type, input_mention.target_id)
        if identity in seen:
            raise DomainError(
                "duplicate_mention", "Une mention cible apparaît plusieurs fois", status_code=422
            )
        seen.add(identity)
        target_agent: Agent | None = None
        if input_mention.target_type == "agent":
            target_agent = await db.scalar(
                select(Agent)
                .join(
                    AgentChannelMembership,
                    (AgentChannelMembership.agent_id == Agent.id)
                    & (AgentChannelMembership.channel_id == channel_id),
                )
                .where(
                    Agent.id == input_mention.target_id,
                    Agent.tenant_id == tenant_id,
                    Agent.space_id == channel.space_id,
                    Agent.status == "active",
                )
            )
            if target_agent is None:
                raise DomainError(
                    "mention_target_not_member",
                    "L’agent ciblé n’est pas un membre actif de ce channel",
                    status_code=403,
                )
            if target_agent.handle != input_mention.handle_at_creation:
                raise ConflictError(
                    "mention_stale",
                    "Le handle de la mention a changé; sélectionnez de nouveau l’agent",
                )
        else:
            target_member = await db.scalar(
                select(ChannelMember).where(
                    ChannelMember.tenant_id == tenant_id,
                    ChannelMember.channel_id == channel_id,
                    ChannelMember.actor_id == input_mention.target_id,
                )
            )
            if target_member is None:
                raise DomainError(
                    "mention_target_not_member",
                    "La personne ciblée n’est pas membre de ce channel",
                    status_code=403,
                )
        mention = MessageMention(
            tenant_id=tenant_id,
            space_id=channel.space_id,
            channel_id=channel.id,
            message_id=message.id,
            target_type=input_mention.target_type,
            target_id=input_mention.target_id,
            handle_at_creation=input_mention.handle_at_creation,
            created_at=utcnow(),
        )
        db.add(mention)
        await db.flush()
        mentions.append(mention)
        add_internal_event(
            db,
            event_type="mention.created",
            tenant_id=tenant_id,
            space_id=channel.space_id,
            channel_id=channel.id,
            actor_type=author_type,
            actor_id=author_actor_id,
            trace_id=trace.id if trace else None,
            correlation_id=correlation_id,
            idempotency_key=f"mention.created:{mention.id}",
            payload={
                "message_id": str(message.id),
                "mention_id": str(mention.id),
                "target_type": input_mention.target_type,
                "target_id": str(input_mention.target_id),
            },
        )
        if target_agent is None or trace is None:
            continue
        check = await check_delivery_allowed(
            db,
            trace=trace,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent.id,
            depth=depth,
            message_hash=content_hash,
        )
        if not check.allowed:
            blocked_reason = check.reason
            trace.status = "limit_reached"
            trace.stop_reason = check.reason
            trace.completed_at = utcnow()
            add_internal_event(
                db,
                event_type="trace.limit_reached",
                tenant_id=tenant_id,
                space_id=channel.space_id,
                channel_id=channel.id,
                actor_type="system",
                actor_id=None,
                trace_id=trace.id,
                correlation_id=correlation_id,
                idempotency_key=f"trace.limit_reached:{trace.id}:{message.id}:{check.reason}",
                payload={"reason": check.reason, "target_agent_id": str(target_agent.id)},
            )
            continue
        queue_key = f"queue:{target_agent.id}:{channel.id}"
        delivery = Delivery(
            tenant_id=tenant_id,
            space_id=channel.space_id,
            channel_id=channel.id,
            thread_id=data.thread_id,
            message_id=message.id,
            mention_id=mention.id,
            target_agent_id=target_agent.id,
            source_agent_id=source_agent_id,
            trace_id=trace.id,
            parent_delivery_id=parent_delivery_id,
            task_id=task_id,
            status="pending",
            queue_key=queue_key,
            idempotency_key=f"delivery:{message.id}:{target_agent.id}",
            normalized_message_hash=content_hash,
            depth=depth,
            available_at=utcnow(),
            retry_policy={"max_attempts": 5, "base_seconds": 2, "max_seconds": 300},
        )
        db.add(delivery)
        await db.flush()
        await _upsert_queue(
            db,
            tenant_id=tenant_id,
            space_id=channel.space_id,
            agent_id=target_agent.id,
            channel_id=channel.id,
            queue_key=queue_key,
        )
        participant = await db.scalar(
            select(TraceParticipant).where(
                TraceParticipant.trace_id == trace.id,
                TraceParticipant.agent_id == target_agent.id,
            )
        )
        if participant is None:
            db.add(
                TraceParticipant(
                    tenant_id=tenant_id,
                    trace_id=trace.id,
                    agent_id=target_agent.id,
                    parent_agent_id=source_agent_id,
                    first_delivery_id=delivery.id,
                )
            )
        trace.max_depth_seen = max(trace.max_depth_seen, depth)
        if source_agent_id is not None:
            trace.delegation_count += 1
        add_internal_event(
            db,
            event_type="delivery.created",
            tenant_id=tenant_id,
            space_id=channel.space_id,
            channel_id=channel.id,
            actor_type=author_type,
            actor_id=author_actor_id,
            trace_id=trace.id,
            correlation_id=correlation_id,
            idempotency_key=f"delivery.created:{delivery.id}",
            payload={
                "delivery_id": str(delivery.id),
                "agent_id": str(target_agent.id),
                "queue_key": queue_key,
            },
        )

    add_internal_event(
        db,
        event_type="message.created",
        tenant_id=tenant_id,
        space_id=channel.space_id,
        channel_id=channel.id,
        actor_type=author_type,
        actor_id=author_actor_id,
        trace_id=trace.id if trace else None,
        correlation_id=correlation_id,
        idempotency_key=f"message.created:{message.id}",
        payload={
            "message_id": str(message.id),
            "mention_count": len(mentions),
            "blocked_reason": blocked_reason,
        },
    )
    add_audit_event(
        db,
        tenant_id=tenant_id,
        actor_type=author_type,
        actor_id=author_actor_id,
        action="message.posted",
        resource_type="message",
        resource_id=message.id,
        trace_id=trace.id if trace else None,
        details={"channel_id": str(channel.id), "mention_count": len(mentions)},
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return CreatedMessage(message, mentions)


async def list_channel_messages(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    actor_id: UUID,
    channel_id: UUID,
    thread_id: UUID | None = None,
    before: UUID | None = None,
    limit: int = 50,
) -> list[Message]:
    await require_channel_membership(
        db, tenant_id=tenant_id, channel_id=channel_id, actor_id=actor_id
    )
    statement = select(Message).where(
        Message.tenant_id == tenant_id,
        Message.channel_id == channel_id,
    )
    if thread_id is not None:
        statement = statement.where(Message.thread_id == thread_id)
    if before is not None:
        cursor = await db.scalar(
            select(Message).where(
                Message.id == before,
                Message.tenant_id == tenant_id,
                Message.channel_id == channel_id,
            )
        )
        if cursor is None:
            raise NotFoundError("message", before)
        statement = statement.where(Message.created_at < cursor.created_at)
    messages = list(
        (
            await db.scalars(
                statement.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
            )
        ).all()
    )
    messages.reverse()
    return messages


async def serialize_message(db: AsyncSession, message: Message) -> dict[str, object]:
    actor = await db.get(Actor, message.author_id)
    agent = await db.scalar(select(Agent).where(Agent.actor_id == message.author_id))
    mentions = await _get_mentions(db, message.id)
    return {
        "id": message.id,
        "tenant_id": message.tenant_id,
        "space_id": message.space_id,
        "channel_id": message.channel_id,
        "thread_id": message.thread_id,
        "author_type": message.author_type,
        "author_id": message.author_id,
        "author_display_name": actor.display_name if actor else None,
        "author_handle": agent.handle if agent else None,
        "content": message.content,
        "reply_to_id": message.reply_to_id,
        "trace_id": message.trace_id,
        "task_id": message.task_id,
        "expects_response": message.expects_response,
        "is_technical": message.is_technical,
        "mentions": mentions,
        "created_at": message.created_at,
    }
