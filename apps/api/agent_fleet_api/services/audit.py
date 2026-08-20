from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_governance import AuditEvent, InternalEvent
from packages.shared.time import utcnow


def add_audit_event(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    actor_type: str,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | None,
    details: dict[str, Any] | None = None,
    trace_id: UUID | None = None,
    outcome: str = "success",
    ip_hash: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        details=details or {},
        trace_id=trace_id,
        ip_hash=ip_hash,
        created_at=utcnow(),
    )
    db.add(event)
    return event


def add_internal_event(
    db: AsyncSession,
    *,
    event_type: str,
    tenant_id: UUID,
    actor_type: str,
    actor_id: UUID | None,
    idempotency_key: str,
    payload: dict[str, Any],
    space_id: UUID | None = None,
    channel_id: UUID | None = None,
    trace_id: UUID | None = None,
    correlation_id: UUID | None = None,
    causation_id: UUID | None = None,
) -> InternalEvent:
    event = InternalEvent(
        event_type=event_type,
        event_version=1,
        tenant_id=tenant_id,
        space_id=space_id,
        channel_id=channel_id,
        actor_type=actor_type,
        actor_id=actor_id,
        trace_id=trace_id,
        correlation_id=correlation_id or uuid4(),
        causation_id=causation_id,
        idempotency_key=idempotency_key,
        occurred_at=utcnow(),
        payload=payload,
    )
    db.add(event)
    return event
