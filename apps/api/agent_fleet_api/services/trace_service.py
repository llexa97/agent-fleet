from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_collaboration import Trace
from apps.api.agent_fleet_api.models_execution import AgentSession, Delivery, DeliveryQueue
from apps.api.agent_fleet_api.models_infrastructure import WorkerCommand
from apps.api.agent_fleet_api.security import Principal
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from packages.shared.errors import DomainError, NotFoundError
from packages.shared.time import utcnow


async def list_traces(
    db: AsyncSession,
    principal: Principal,
    *,
    space_id: UUID | None = None,
) -> list[Trace]:
    statement = select(Trace).where(Trace.tenant_id == principal.tenant_id)
    if space_id is not None:
        statement = statement.where(Trace.space_id == space_id)
    return list((await db.scalars(statement.order_by(Trace.created_at.desc()).limit(200))).all())


async def get_trace(db: AsyncSession, tenant_id: UUID, trace_id: UUID) -> Trace:
    trace = await db.scalar(select(Trace).where(Trace.id == trace_id, Trace.tenant_id == tenant_id))
    if trace is None:
        raise NotFoundError("trace", trace_id)
    return trace


async def transition_trace(
    db: AsyncSession,
    principal: Principal,
    trace_id: UUID,
    action: str,
) -> Trace:
    trace = await get_trace(db, principal.tenant_id, trace_id)
    allowed: dict[str, set[str]] = {
        "pause": {"running"},
        "resume": {"paused", "waiting_approval"},
        "cancel": {"running", "paused", "waiting_approval"},
    }
    if trace.status not in allowed[action]:
        raise DomainError(
            "invalid_trace_transition",
            f"Impossible de {action} une trace {trace.status}",
            status_code=409,
        )
    if action == "pause":
        trace.status = "paused"
        trace.paused_at = utcnow()
    elif action == "resume":
        trace.status = "running"
        trace.paused_at = None
        trace.stop_reason = None
    else:
        trace.status = "cancelled"
        trace.stop_reason = "cancelled_by_human"
        trace.completed_at = utcnow()
        active = list(
            (
                await db.scalars(
                    select(Delivery).where(
                        Delivery.trace_id == trace.id,
                        Delivery.status.in_(
                            [
                                "pending",
                                "claimed",
                                "dispatched",
                                "processing",
                                "waiting_approval",
                                "retry_scheduled",
                            ]
                        ),
                    )
                )
            ).all()
        )
        for delivery in active:
            delivery.status = "cancelled"
            delivery.active_slot = False
            delivery.lease_owner = None
            delivery.lease_expires_at = None
        await db.flush()
        for queue_key in {delivery.queue_key for delivery in active}:
            queue = await db.scalar(
                select(DeliveryQueue).where(DeliveryQueue.queue_key == queue_key)
            )
            if queue is None:
                continue
            queue.pending_count = int(
                await db.scalar(
                    select(func.count(Delivery.id)).where(
                        Delivery.queue_key == queue_key,
                        Delivery.status.in_(["pending", "retry_scheduled"]),
                    )
                )
                or 0
            )
            queue.next_wake_at = utcnow() if queue.pending_count else None
            queue.lease_owner = None
            queue.lease_expires_at = None
        sessions = list(
            (
                await db.scalars(
                    select(AgentSession).where(
                        AgentSession.trace_id == trace.id,
                        AgentSession.status.in_(["starting", "active", "waiting_approval"]),
                    )
                )
            ).all()
        )
        for session in sessions:
            db.add(
                WorkerCommand(
                    tenant_id=principal.tenant_id,
                    worker_id=session.worker_id,
                    trace_id=trace.id,
                    session_id=session.id,
                    command_type="cancel_prompt",
                    idempotency_key=f"cancel:{trace.id}:{session.id}",
                    payload={"reason": "cancelled_by_human"},
                    available_at=utcnow(),
                )
            )
    event_suffix = {"pause": "paused", "resume": "resumed", "cancel": "cancelled"}[action]
    event_type = f"trace.{event_suffix}"
    add_internal_event(
        db,
        event_type=event_type,
        tenant_id=principal.tenant_id,
        space_id=trace.space_id,
        channel_id=trace.channel_id,
        actor_type="human",
        actor_id=principal.actor_id,
        trace_id=trace.id,
        idempotency_key=f"{event_type}:{trace.id}:{uuid4()}",
        payload={"trace_id": str(trace.id), "status": trace.status},
    )
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action=event_type,
        resource_type="trace",
        resource_id=trace.id,
        trace_id=trace.id,
    )
    await db.commit()
    return trace
