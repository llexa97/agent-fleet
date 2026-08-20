from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.config import get_settings
from apps.api.agent_fleet_api.models_collaboration import Trace
from apps.api.agent_fleet_api.models_execution import (
    AgentSession,
    Delivery,
    PermissionDecision,
    PermissionRequest,
)
from apps.api.agent_fleet_api.models_infrastructure import WorkerCommand
from apps.api.agent_fleet_api.schemas import PermissionDecisionInput
from apps.api.agent_fleet_api.security import Principal
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from packages.shared.errors import ConflictError, NotFoundError
from packages.shared.time import utcnow


async def decide_permission(
    db: AsyncSession,
    principal: Principal,
    request_id: UUID,
    data: PermissionDecisionInput,
) -> PermissionRequest:
    request = await db.scalar(
        select(PermissionRequest).where(
            PermissionRequest.id == request_id,
            PermissionRequest.tenant_id == principal.tenant_id,
        )
    )
    if request is None:
        raise NotFoundError("permission_request", request_id)
    if request.status != "pending":
        raise ConflictError("permission_already_decided", "Cette demande a déjà été traitée")
    now = utcnow()
    approved = data.decision.value != "deny"
    request.status = "approved" if approved else "denied"
    decision = PermissionDecision(
        tenant_id=principal.tenant_id,
        permission_request_id=request.id,
        decided_by_actor_id=principal.actor_id,
        decision=data.decision.value,
        scope=data.decision.value,
        reason=data.reason,
        created_at=now,
    )
    db.add(decision)
    session = await db.get(AgentSession, request.session_id)
    if session is None:
        raise NotFoundError("session", request.session_id)
    delivery = await db.get(Delivery, request.delivery_id) if request.delivery_id else None
    session.status = "active"
    if delivery is not None:
        delivery.status = "processing"
        delivery.lease_expires_at = now + timedelta(seconds=get_settings().delivery_lease_seconds)
    trace = await db.get(Trace, request.trace_id)
    if trace is not None and trace.status == "waiting_approval":
        trace.status = "running"
    command_type = "approve_permission" if approved else "deny_permission"
    db.add(
        WorkerCommand(
            tenant_id=principal.tenant_id,
            worker_id=session.worker_id,
            trace_id=session.trace_id,
            session_id=session.id,
            command_type=command_type,
            idempotency_key=f"permission:{request.id}:{data.decision.value}",
            payload={
                "permission_request_id": str(request.id),
                "external_request_id": request.external_request_id,
                "decision": data.decision.value,
            },
            available_at=now,
        )
    )
    add_internal_event(
        db,
        event_type="permission.decided",
        tenant_id=principal.tenant_id,
        space_id=request.space_id,
        actor_type="human",
        actor_id=principal.actor_id,
        trace_id=request.trace_id,
        idempotency_key=f"permission.decided:{request.id}",
        payload={"permission_request_id": str(request.id), "decision": data.decision.value},
    )
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="permission.decided",
        resource_type="permission_request",
        resource_id=request.id,
        trace_id=request.trace_id,
        details={"decision": data.decision.value, "capability": request.capability},
    )
    await db.commit()
    return request
