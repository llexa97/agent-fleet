import asyncio
import hashlib
import hmac
import ipaddress
import os
import socket
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import structlog
from pydantic import TypeAdapter
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.agent_fleet_api.models_collaboration import (
    AgentChannelMembership,
    Channel,
    ChannelMember,
    Task,
    Trace,
)
from apps.api.agent_fleet_api.models_governance import (
    InternalEvent,
    Workflow,
    WorkflowEventReceipt,
    WorkflowRun,
)
from apps.api.agent_fleet_api.models_identity import Actor, Agent, Space
from apps.api.agent_fleet_api.models_infrastructure import Workspace
from apps.api.agent_fleet_api.schemas import (
    AgentMentionedTrigger,
    ApiModel,
    AssignTaskAction,
    CallWebhookAction,
    CreateTaskAction,
    DelayAction,
    InvokeAgentAction,
    ManualTrigger,
    MentionAgentAction,
    MentionInput,
    MessageCreate,
    MessagePostedTrigger,
    PostMessageAction,
    RequestApprovalAction,
    ScheduleTrigger,
    TaskCreate,
    TaskEventTrigger,
    TaskUpdate,
    WebhookTrigger,
    WorkflowAction,
    WorkflowCreate,
    WorkflowRunResume,
    WorkflowUpdate,
)
from apps.api.agent_fleet_api.security import Principal
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from apps.api.agent_fleet_api.services.message_service import post_message
from apps.api.agent_fleet_api.services.orchestration_policy import DEFAULT_TRACE_POLICY
from apps.api.agent_fleet_api.services.task_service import create_task, update_task
from packages.shared.errors import ConflictError, DomainError, ForbiddenError, NotFoundError
from packages.shared.time import as_utc, utcnow

logger = structlog.get_logger(__name__)

EVENT_TRIGGER_TYPES = {
    "message_posted": "message.created",
    "agent_mentioned": "mention.created",
    "task_created": "task.created",
    "task_completed": "task.completed",
}
TRIGGER_CONFIG_MODELS: dict[str, type[ApiModel]] = {
    "message_posted": MessagePostedTrigger,
    "agent_mentioned": AgentMentionedTrigger,
    "task_created": TaskEventTrigger,
    "task_completed": TaskEventTrigger,
    "schedule": ScheduleTrigger,
    "webhook": WebhookTrigger,
    "manual": ManualTrigger,
}
ACTION_ADAPTER: TypeAdapter[WorkflowAction] = TypeAdapter(WorkflowAction)
MAX_WEBHOOK_REQUEST_BYTES = 1_048_576


def _safe_uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def _action_dict(action: WorkflowAction) -> dict[str, Any]:
    return action.model_dump(mode="json")


def _validate_budget_policy(policy: Mapping[str, Any]) -> dict[str, int]:
    allowed = {"max_actions", "max_duration_seconds"}
    unknown = set(policy) - allowed
    if unknown:
        raise DomainError(
            "invalid_workflow_budget",
            "La politique de budget contient des champs inconnus",
            status_code=422,
            details={"fields": sorted(unknown)},
        )
    max_actions = policy.get("max_actions", 100)
    max_duration = policy.get("max_duration_seconds", 7_200)
    if (
        not isinstance(max_actions, int)
        or isinstance(max_actions, bool)
        or not 1 <= max_actions <= 100
    ):
        raise DomainError(
            "invalid_workflow_budget",
            "max_actions doit être compris entre 1 et 100",
            status_code=422,
        )
    if (
        not isinstance(max_duration, int)
        or isinstance(max_duration, bool)
        or not 1 <= max_duration <= 86_400
    ):
        raise DomainError(
            "invalid_workflow_budget",
            "max_duration_seconds doit être compris entre 1 et 86400",
            status_code=422,
        )
    return {"max_actions": max_actions, "max_duration_seconds": max_duration}


def _validate_url_syntax(action: CallWebhookAction) -> None:
    parsed = urlsplit(action.url)
    allowed_schemes = {"https", "http"} if action.allow_http else {"https"}
    if parsed.scheme.casefold() not in allowed_schemes:
        raise DomainError(
            "unsafe_webhook_url",
            "Le webhook sortant doit utiliser HTTPS",
            status_code=422,
        )
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise DomainError("unsafe_webhook_url", "L’URL du webhook est invalide", status_code=422)
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if _is_unsafe_ip(literal):
        raise DomainError(
            "unsafe_webhook_url",
            "Les adresses privées ou réservées sont interdites",
            status_code=422,
        )


def _is_unsafe_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_unsafe_ip(address.ipv4_mapped)
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


async def _validate_resolved_webhook(action: CallWebhookAction) -> None:
    _validate_url_syntax(action)
    parsed = urlsplit(action.url)
    assert parsed.hostname is not None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise DomainError(
            "webhook_dns_failed", "Le nom du webhook ne peut pas être résolu", status_code=422
        ) from exc
    if not addresses:
        raise DomainError(
            "webhook_dns_failed", "Le nom du webhook ne peut pas être résolu", status_code=422
        )
    for address in addresses:
        try:
            resolved = ipaddress.ip_address(address[4][0].split("%", maxsplit=1)[0])
        except ValueError as exc:
            raise DomainError(
                "webhook_dns_failed", "Une adresse DNS invalide a été reçue", status_code=422
            ) from exc
        if _is_unsafe_ip(resolved):
            raise ForbiddenError(
                "Le webhook résout vers une adresse privée ou réservée",
                code="webhook_ssrf_blocked",
            )


async def _send_webhook(
    action: CallWebhookAction,
    *,
    run: WorkflowRun,
) -> dict[str, Any]:
    await _validate_resolved_webhook(action)
    request_body = {
        "workflow_run_id": str(run.id),
        "trace_id": str(run.trace_id),
        "trigger": run.trigger_payload,
        "payload": action.payload,
    }
    request_size = len(httpx.Request("POST", action.url, json=request_body).content)
    if request_size > MAX_WEBHOOK_REQUEST_BYTES:
        raise DomainError(
            "webhook_request_too_large",
            "Le corps du webhook sortant dépasse la limite autorisée",
            status_code=422,
        )
    digest = hashlib.sha256()
    received = 0
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(action.timeout_seconds),
        trust_env=False,
    ) as client:
        async with client.stream(
            "POST",
            action.url,
            json=request_body,
            headers={
                "Idempotency-Key": f"workflow:{run.id}:{run.current_action}",
                "User-Agent": "Agent-Fleet-Workflow/0.1",
            },
        ) as response:
            if 300 <= response.status_code < 400:
                raise DomainError(
                    "webhook_redirect_blocked",
                    "Les redirections de webhook sont interdites",
                    status_code=502,
                )
            if response.status_code >= 400:
                raise DomainError(
                    "webhook_remote_error",
                    "Le webhook distant a retourné une erreur",
                    status_code=502,
                    details={"status_code": response.status_code},
                )
            async for chunk in response.aiter_bytes():
                received += len(chunk)
                if received > action.max_response_bytes:
                    raise DomainError(
                        "webhook_response_too_large",
                        "La réponse du webhook dépasse la limite autorisée",
                        status_code=502,
                    )
                digest.update(chunk)
            return {
                "status_code": response.status_code,
                "response_bytes": received,
                "response_sha256": digest.hexdigest(),
            }


async def _require_space(db: AsyncSession, tenant_id: UUID, space_id: UUID) -> Space:
    space = await db.scalar(select(Space).where(Space.id == space_id, Space.tenant_id == tenant_id))
    if space is None:
        raise NotFoundError("space", space_id)
    return space


async def _require_channel(db: AsyncSession, workflow: Workflow, channel_id: UUID) -> Channel:
    channel = await db.scalar(
        select(Channel).where(
            Channel.id == channel_id,
            Channel.tenant_id == workflow.tenant_id,
            Channel.space_id == workflow.space_id,
            Channel.is_archived.is_(False),
        )
    )
    if channel is None:
        raise NotFoundError("channel", channel_id)
    membership = await db.scalar(
        select(ChannelMember).where(
            ChannelMember.channel_id == channel.id,
            ChannelMember.actor_id == workflow.actor_id,
        )
    )
    if membership is None:
        db.add(
            ChannelMember(
                tenant_id=workflow.tenant_id,
                space_id=workflow.space_id,
                channel_id=channel.id,
                actor_id=workflow.actor_id,
                role="member",
            )
        )
        await db.flush()
    return channel


async def _require_agent(
    db: AsyncSession, workflow: Workflow, agent_id: UUID, channel_id: UUID | None = None
) -> Agent:
    agent = await db.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == workflow.tenant_id,
            Agent.space_id == workflow.space_id,
            Agent.status == "active",
        )
    )
    if agent is None:
        raise NotFoundError("agent", agent_id)
    if channel_id is not None:
        member = await db.scalar(
            select(AgentChannelMembership.id).where(
                AgentChannelMembership.agent_id == agent.id,
                AgentChannelMembership.channel_id == channel_id,
            )
        )
        if member is None:
            raise ForbiddenError("L’agent ciblé n’est pas membre du channel")
    return agent


async def _validate_configuration(db: AsyncSession, workflow: Workflow) -> None:
    config_model = TRIGGER_CONFIG_MODELS.get(workflow.trigger_type)
    if config_model is None:
        raise DomainError(
            "unsupported_workflow_trigger",
            "Trigger de workflow non supporté",
            status_code=422,
        )
    config = config_model.model_validate(workflow.trigger_config)
    budget = _validate_budget_policy(workflow.budget_policy)
    if len(workflow.actions) > budget["max_actions"]:
        raise DomainError(
            "invalid_workflow_budget",
            "Le workflow contient plus d’actions que max_actions",
            status_code=422,
        )
    channel_ids = list(getattr(config, "channel_ids", []))
    for channel_id in channel_ids:
        await _require_channel(db, workflow, channel_id)

    for raw_action in workflow.actions:
        parsed = _parse_action(raw_action)
        if isinstance(parsed, (PostMessageAction, MentionAgentAction, InvokeAgentAction)):
            await _require_channel(db, workflow, parsed.channel_id)
        if isinstance(parsed, (MentionAgentAction, InvokeAgentAction)):
            await _require_agent(db, workflow, parsed.agent_id, parsed.channel_id)
        if isinstance(parsed, CreateTaskAction):
            if parsed.channel_id is not None:
                await _require_channel(db, workflow, parsed.channel_id)
            if parsed.assigned_agent_id is not None:
                await _require_agent(db, workflow, parsed.assigned_agent_id, parsed.channel_id)
            if parsed.workspace_id is not None:
                workspace = await db.scalar(
                    select(Workspace.id).where(
                        Workspace.id == parsed.workspace_id,
                        Workspace.tenant_id == workflow.tenant_id,
                        Workspace.space_id == workflow.space_id,
                    )
                )
                if workspace is None:
                    raise NotFoundError("workspace", parsed.workspace_id)
        if isinstance(parsed, AssignTaskAction):
            if (
                await db.scalar(
                    select(Task.id).where(
                        Task.id == parsed.task_id,
                        Task.tenant_id == workflow.tenant_id,
                        Task.space_id == workflow.space_id,
                    )
                )
                is None
            ):
                raise NotFoundError("task", parsed.task_id)
            await _require_agent(db, workflow, parsed.agent_id)
        if isinstance(parsed, CallWebhookAction):
            _validate_url_syntax(parsed)


async def create_workflow(db: AsyncSession, principal: Principal, data: WorkflowCreate) -> Workflow:
    await _require_space(db, principal.tenant_id, data.space_id)
    existing_name = await db.scalar(
        select(Workflow.id).where(
            Workflow.space_id == data.space_id,
            Workflow.name == data.name.strip(),
        )
    )
    if existing_name is not None:
        raise ConflictError("workflow_name_exists", "Un workflow porte déjà ce nom dans cet espace")
    actor = Actor(
        tenant_id=principal.tenant_id,
        space_id=data.space_id,
        actor_type="workflow",
        display_name=f"Workflow · {data.name}",
        is_active=True,
    )
    db.add(actor)
    await db.flush()
    workflow = Workflow(
        tenant_id=principal.tenant_id,
        space_id=data.space_id,
        actor_id=actor.id,
        name=data.name.strip(),
        description=data.description,
        status="draft",
        trigger_type=data.trigger_type,
        trigger_config=data.trigger_config,
        actions=[_action_dict(action) for action in data.actions],
        budget_policy=_validate_budget_policy(data.budget_policy),
        webhook_secret_ref=data.webhook_secret_ref,
        created_by_actor_id=principal.actor_id,
    )
    db.add(workflow)
    await db.flush()
    await _validate_configuration(db, workflow)
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="workflow.created",
        resource_type="workflow",
        resource_id=workflow.id,
    )
    add_internal_event(
        db,
        event_type="workflow.created",
        tenant_id=principal.tenant_id,
        space_id=workflow.space_id,
        actor_type="human",
        actor_id=principal.actor_id,
        idempotency_key=f"workflow.created:{workflow.id}",
        payload={"workflow_id": str(workflow.id)},
    )
    await db.commit()
    return workflow


async def get_workflow(db: AsyncSession, tenant_id: UUID, workflow_id: UUID) -> Workflow:
    workflow = await db.scalar(
        select(Workflow).where(Workflow.id == workflow_id, Workflow.tenant_id == tenant_id)
    )
    if workflow is None:
        raise NotFoundError("workflow", workflow_id)
    return workflow


async def list_workflows(
    db: AsyncSession, tenant_id: UUID, *, space_id: UUID | None = None
) -> list[Workflow]:
    statement = select(Workflow).where(Workflow.tenant_id == tenant_id)
    if space_id is not None:
        statement = statement.where(Workflow.space_id == space_id)
    return list((await db.scalars(statement.order_by(Workflow.name))).all())


async def update_workflow(
    db: AsyncSession,
    principal: Principal,
    workflow_id: UUID,
    data: WorkflowUpdate,
) -> Workflow:
    workflow = await get_workflow(db, principal.tenant_id, workflow_id)
    if workflow.status == "active":
        raise ConflictError("workflow_active", "Mettez le workflow en pause avant de le modifier")
    changes = data.model_dump(exclude_unset=True)
    if "actions" in changes:
        workflow.actions = [_action_dict(action) for action in data.actions or []]
        changes.pop("actions")
    if "trigger_config" in changes:
        raw_config = changes.pop("trigger_config")
        workflow.trigger_config = (
            TRIGGER_CONFIG_MODELS[workflow.trigger_type]
            .model_validate(raw_config)
            .model_dump(mode="json")
        )
    if "budget_policy" in changes:
        changes["budget_policy"] = _validate_budget_policy(changes["budget_policy"])
    for field, value in changes.items():
        setattr(workflow, field, value)
    actor = await db.get(Actor, workflow.actor_id)
    if actor is not None:
        actor.display_name = f"Workflow · {workflow.name}"
    await _validate_configuration(db, workflow)
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="workflow.updated",
        resource_type="workflow",
        resource_id=workflow.id,
    )
    await db.commit()
    return workflow


async def transition_workflow(
    db: AsyncSession, principal: Principal, workflow_id: UUID, target: str
) -> Workflow:
    workflow = await get_workflow(db, principal.tenant_id, workflow_id)
    now = utcnow()
    if target == "active":
        if workflow.status not in {"draft", "paused"}:
            raise ConflictError(
                "invalid_workflow_transition", "Ce workflow ne peut pas être activé"
            )
        await _validate_configuration(db, workflow)
        workflow.status = "active"
        if workflow.trigger_type == "schedule":
            config = ScheduleTrigger.model_validate(workflow.trigger_config)
            workflow.next_run_at = config.start_at or now
    elif target == "paused":
        if workflow.status != "active":
            raise ConflictError(
                "invalid_workflow_transition",
                "Seul un workflow actif peut être mis en pause",
            )
        workflow.status = "paused"
        workflow.next_run_at = None
    else:
        raise DomainError("invalid_workflow_transition", "Transition inconnue", status_code=422)
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action=f"workflow.{workflow.status}",
        resource_type="workflow",
        resource_id=workflow.id,
    )
    add_internal_event(
        db,
        event_type=f"workflow.{workflow.status}",
        tenant_id=workflow.tenant_id,
        space_id=workflow.space_id,
        actor_type="human",
        actor_id=principal.actor_id,
        idempotency_key=f"workflow.{workflow.status}:{workflow.id}:{now.isoformat()}",
        payload={"workflow_id": str(workflow.id), "status": workflow.status},
    )
    await db.commit()
    return workflow


async def create_workflow_run(
    db: AsyncSession,
    workflow: Workflow,
    *,
    trigger_type: str,
    idempotency_key: str,
    trigger_payload: dict[str, Any],
    actor_id: UUID | None,
    actor_type: str,
    trigger_event: InternalEvent | None = None,
    commit: bool = True,
) -> WorkflowRun:
    if workflow.status != "active":
        raise ConflictError("workflow_inactive", "Le workflow doit être actif")
    existing = await db.scalar(
        select(WorkflowRun).where(
            WorkflowRun.tenant_id == workflow.tenant_id,
            WorkflowRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.trigger_type != trigger_type or existing.trigger_payload != trigger_payload:
            raise ConflictError(
                "idempotency_key_reused",
                "Cette clé d’idempotence a déjà été utilisée avec un autre déclencheur",
            )
        return existing
    parent_trace_id = trigger_event.trace_id if trigger_event is not None else None
    trace = Trace(
        tenant_id=workflow.tenant_id,
        space_id=workflow.space_id,
        channel_id=trigger_event.channel_id if trigger_event is not None else None,
        parent_trace_id=parent_trace_id,
        trigger_message_id=_safe_uuid(trigger_payload.get("message_id")),
        trigger_task_id=_safe_uuid(trigger_payload.get("task_id")),
        initiator_actor_id=workflow.actor_id,
        status="running",
        policy=dict(DEFAULT_TRACE_POLICY),
    )
    db.add(trace)
    await db.flush()
    run = WorkflowRun(
        tenant_id=workflow.tenant_id,
        space_id=workflow.space_id,
        workflow_id=workflow.id,
        trace_id=trace.id,
        status="pending",
        trigger_type=trigger_type,
        trigger_event_id=trigger_event.id if trigger_event is not None else None,
        trigger_payload=trigger_payload,
        idempotency_key=idempotency_key,
        state={"results": {}},
        available_at=utcnow(),
    )
    db.add(run)
    await db.flush()
    workflow.last_run_at = utcnow()
    add_internal_event(
        db,
        event_type="workflow.run_created",
        tenant_id=workflow.tenant_id,
        space_id=workflow.space_id,
        channel_id=trace.channel_id,
        actor_type=actor_type,
        actor_id=actor_id,
        trace_id=trace.id,
        causation_id=trigger_event.id if trigger_event is not None else None,
        idempotency_key=f"workflow.run_created:{run.id}",
        payload={"workflow_id": str(workflow.id), "run_id": str(run.id)},
    )
    add_audit_event(
        db,
        tenant_id=workflow.tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="workflow.run_created",
        resource_type="workflow_run",
        resource_id=run.id,
        trace_id=trace.id,
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return run


async def _event_matches(db: AsyncSession, workflow: Workflow, event: InternalEvent) -> bool:
    if event.space_id != workflow.space_id or event.actor_id == workflow.actor_id:
        return False
    config = TRIGGER_CONFIG_MODELS[workflow.trigger_type].model_validate(workflow.trigger_config)
    channel_ids = getattr(config, "channel_ids", [])
    if channel_ids and event.channel_id not in channel_ids:
        return False
    if isinstance(config, MessagePostedTrigger):
        return not config.actor_types or event.actor_type in config.actor_types
    if isinstance(config, AgentMentionedTrigger):
        target_id = _safe_uuid(event.payload.get("target_id"))
        return not config.target_agent_ids or target_id in config.target_agent_ids
    if isinstance(config, TaskEventTrigger):
        task_id = _safe_uuid(event.payload.get("task_id"))
        assigned = (
            await db.scalar(
                select(Task.assigned_agent_id).where(
                    Task.id == task_id,
                    Task.tenant_id == workflow.tenant_id,
                    Task.space_id == workflow.space_id,
                )
            )
            if task_id is not None
            else None
        )
        return not config.assigned_agent_ids or assigned in config.assigned_agent_ids
    return True


async def _trace_allows_workflow(
    db: AsyncSession, workflow: Workflow, parent_trace_id: UUID | None
) -> bool:
    """Bloque A→B→A et les chaînes de workflows trop profondes."""
    cursor = parent_trace_id
    visited: set[UUID] = set()
    for _depth in range(10):
        if cursor is None:
            return True
        if cursor in visited:
            return False
        visited.add(cursor)
        prior = await db.scalar(
            select(WorkflowRun.id).where(
                WorkflowRun.workflow_id == workflow.id,
                WorkflowRun.trace_id == cursor,
            )
        )
        if prior is not None:
            return False
        trace = await db.get(Trace, cursor)
        if trace is None:
            return True
        cursor = trace.parent_trace_id
    return cursor is None


async def trigger_event_workflows_once(db: AsyncSession, *, limit_per_workflow: int = 100) -> int:
    workflows = list(
        (
            await db.scalars(
                select(Workflow).where(
                    Workflow.status == "active",
                    Workflow.trigger_type.in_(tuple(EVENT_TRIGGER_TYPES)),
                )
            )
        ).all()
    )
    created = 0
    for workflow in workflows:
        event_type = EVENT_TRIGGER_TYPES[workflow.trigger_type]
        events = list(
            (
                await db.scalars(
                    select(InternalEvent)
                    .where(
                        InternalEvent.tenant_id == workflow.tenant_id,
                        InternalEvent.space_id == workflow.space_id,
                        InternalEvent.event_type == event_type,
                        InternalEvent.occurred_at >= workflow.created_at,
                        ~exists(
                            select(WorkflowEventReceipt.id).where(
                                WorkflowEventReceipt.workflow_id == workflow.id,
                                WorkflowEventReceipt.event_id == InternalEvent.id,
                            )
                        ),
                    )
                    .order_by(InternalEvent.occurred_at, InternalEvent.id)
                    .limit(limit_per_workflow)
                )
            ).all()
        )
        for event in events:
            matched = await _event_matches(db, workflow, event)
            if matched:
                matched = await _trace_allows_workflow(db, workflow, event.trace_id)
            db.add(
                WorkflowEventReceipt(
                    tenant_id=workflow.tenant_id,
                    workflow_id=workflow.id,
                    event_id=event.id,
                    matched=matched,
                )
            )
            if not matched:
                continue
            await create_workflow_run(
                db,
                workflow,
                trigger_type=workflow.trigger_type,
                idempotency_key=f"workflow:{workflow.id}:event:{event.id}",
                trigger_payload=dict(event.payload),
                actor_id=event.actor_id,
                actor_type=event.actor_type,
                trigger_event=event,
                commit=False,
            )
            created += 1
    await db.commit()
    return created


async def trigger_schedules_once(db: AsyncSession, *, limit: int = 100) -> int:
    now = utcnow()
    workflows = list(
        (
            await db.scalars(
                select(Workflow)
                .where(
                    Workflow.status == "active",
                    Workflow.trigger_type == "schedule",
                    Workflow.next_run_at.is_not(None),
                    Workflow.next_run_at <= now,
                )
                .order_by(Workflow.next_run_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        ).all()
    )
    for workflow in workflows:
        due_at = as_utc(workflow.next_run_at or now)
        config = ScheduleTrigger.model_validate(workflow.trigger_config)
        workflow.next_run_at = max(
            due_at + timedelta(seconds=config.interval_seconds),
            now + timedelta(seconds=config.interval_seconds),
        )
        await create_workflow_run(
            db,
            workflow,
            trigger_type="schedule",
            idempotency_key=f"workflow:{workflow.id}:schedule:{due_at.isoformat()}",
            trigger_payload={"scheduled_for": due_at.isoformat()},
            actor_id=workflow.actor_id,
            actor_type="workflow",
            commit=False,
        )
    await db.commit()
    return len(workflows)


def _parse_action(raw_action: dict[str, Any]) -> WorkflowAction:
    try:
        return ACTION_ADAPTER.validate_python(raw_action)
    except ValueError as exc:
        raise DomainError(
            "unsupported_workflow_action",
            f"Action de workflow non supportée: {raw_action.get('type')}",
            status_code=422,
        ) from exc


def _record_result(run: WorkflowRun, index: int, result: dict[str, Any]) -> None:
    state = dict(run.state)
    results = dict(state.get("results", {}))
    results[str(index)] = result
    state["results"] = results
    state.pop("wait_kind", None)
    run.state = state
    run.current_action = index + 1


async def _execute_action(
    db: AsyncSession, workflow: Workflow, run: WorkflowRun, action: WorkflowAction
) -> bool:
    index = run.current_action
    if isinstance(action, PostMessageAction):
        await _require_channel(db, workflow, action.channel_id)
        message_result = await post_message(
            db,
            tenant_id=workflow.tenant_id,
            author_actor_id=workflow.actor_id,
            author_type="workflow",
            channel_id=action.channel_id,
            data=MessageCreate(content=action.content),
            idempotency_key=f"workflow:{run.id}:action:{index}",
            trace_id=run.trace_id,
            commit=False,
        )
        _record_result(run, index, {"message_id": str(message_result.message.id)})
        return True
    if isinstance(action, (MentionAgentAction, InvokeAgentAction)):
        await _require_channel(db, workflow, action.channel_id)
        agent = await _require_agent(db, workflow, action.agent_id, action.channel_id)
        task_id = action.task_id if isinstance(action, InvokeAgentAction) else None
        message_result = await post_message(
            db,
            tenant_id=workflow.tenant_id,
            author_actor_id=workflow.actor_id,
            author_type="workflow",
            channel_id=action.channel_id,
            data=MessageCreate(
                content=action.content,
                mentions=[
                    MentionInput(
                        target_type="agent",
                        target_id=agent.id,
                        handle_at_creation=agent.handle,
                    )
                ],
                expects_response=True,
            ),
            idempotency_key=f"workflow:{run.id}:action:{index}",
            trace_id=run.trace_id,
            task_id=task_id,
            commit=False,
        )
        _record_result(
            run,
            index,
            {"message_id": str(message_result.message.id), "agent_id": str(agent.id)},
        )
        return True
    if isinstance(action, CreateTaskAction):
        task = await create_task(
            db,
            principal=None,
            actor_type="workflow",
            actor_id=workflow.actor_id,
            tenant_id=workflow.tenant_id,
            data=TaskCreate(
                space_id=workflow.space_id,
                channel_id=action.channel_id,
                trace_id=run.trace_id,
                assigned_agent_id=action.assigned_agent_id,
                title=action.title,
                description=action.description,
                priority=action.priority,
                workspace_id=action.workspace_id,
            ),
            idempotency_key=f"workflow:{run.id}:action:{index}",
            commit=False,
        )
        _record_result(run, index, {"task_id": str(task.id), "status": task.status})
        return True
    if isinstance(action, AssignTaskAction):
        existing_task = await db.scalar(
            select(Task).where(
                Task.id == action.task_id,
                Task.tenant_id == workflow.tenant_id,
                Task.space_id == workflow.space_id,
            )
        )
        if existing_task is None:
            raise NotFoundError("task", action.task_id)
        await _require_agent(db, workflow, action.agent_id, existing_task.channel_id)
        status = "queued" if existing_task.status == "backlog" else None
        updated_task = await update_task(
            db,
            tenant_id=workflow.tenant_id,
            actor_type="workflow",
            actor_id=workflow.actor_id,
            task_id=existing_task.id,
            data=TaskUpdate(status=status, assigned_agent_id=action.agent_id),
            commit=False,
        )
        _record_result(
            run,
            index,
            {"task_id": str(updated_task.id), "status": updated_task.status},
        )
        return True
    if isinstance(action, RequestApprovalAction):
        state = dict(run.state)
        state["wait_kind"] = "approval"
        state["approval"] = {
            "status": "pending",
            "summary": action.summary,
            "details": action.details,
        }
        run.state = state
        run.current_action = index + 1
        run.status = "waiting"
        trace = await db.get(Trace, run.trace_id)
        if trace is not None:
            trace.status = "waiting_approval"
        add_internal_event(
            db,
            event_type="workflow.approval_requested",
            tenant_id=workflow.tenant_id,
            space_id=workflow.space_id,
            actor_type="workflow",
            actor_id=workflow.actor_id,
            trace_id=run.trace_id,
            idempotency_key=f"workflow.approval_requested:{run.id}:{index}",
            payload={
                "workflow_id": str(workflow.id),
                "run_id": str(run.id),
                "summary": action.summary,
            },
        )
        return False
    if isinstance(action, DelayAction):
        _record_result(run, index, {"delay_seconds": action.seconds})
        state = dict(run.state)
        state["wait_kind"] = "delay"
        run.state = state
        run.status = "waiting"
        run.available_at = utcnow() + timedelta(seconds=action.seconds)
        return False
    if isinstance(action, CallWebhookAction):
        webhook_result = await _send_webhook(action, run=run)
        await db.refresh(run, attribute_names=["status"])
        if run.status == "cancelled":
            return False
        _record_result(run, index, webhook_result)
        return True
    raise DomainError(
        "unsupported_workflow_action", "Action de workflow non supportée", status_code=422
    )


async def execute_workflow_run(db: AsyncSession, run: WorkflowRun) -> WorkflowRun:
    workflow = await db.scalar(
        select(Workflow).where(
            Workflow.id == run.workflow_id,
            Workflow.tenant_id == run.tenant_id,
        )
    )
    if workflow is None:
        raise NotFoundError("workflow", run.workflow_id)
    if run.status in {"completed", "failed", "cancelled"}:
        return run
    state = dict(run.state)
    if run.status == "waiting" and state.get("wait_kind") == "approval":
        return run
    now = utcnow()
    run.status = "running"
    run.started_at = run.started_at or now
    trace = await db.get(Trace, run.trace_id)
    if trace is not None and trace.status in {"waiting_approval", "paused"}:
        trace.status = "running"
    policy = _validate_budget_policy(workflow.budget_policy)
    if len(workflow.actions) > policy["max_actions"]:
        raise DomainError(
            "workflow_budget_exceeded", "Le budget d’actions est dépassé", status_code=409
        )
    if as_utc(run.created_at) + timedelta(seconds=policy["max_duration_seconds"]) < now:
        raise DomainError(
            "workflow_budget_exceeded",
            "La durée maximale du workflow est dépassée",
            status_code=409,
        )
    # Une action validée est validée dans sa propre transaction : après un crash,
    # current_action et les effets idempotents reprennent exactement à l'étape suivante.
    await db.commit()
    while run.current_action < len(workflow.actions):
        await db.refresh(run, attribute_names=["status"])
        if run.status == "cancelled":
            return run
        deadline = as_utc(run.created_at) + timedelta(seconds=policy["max_duration_seconds"])
        if deadline < utcnow():
            raise DomainError(
                "workflow_budget_exceeded",
                "La durée maximale du workflow est dépassée",
                status_code=409,
            )
        raw_action = workflow.actions[run.current_action]
        action = _parse_action(raw_action)
        should_continue = await _execute_action(db, workflow, run, action)
        await db.flush()
        if not should_continue:
            await db.commit()
            return run
        await db.commit()
    run.status = "completed"
    run.completed_at = utcnow()
    if trace is not None:
        trace.status = "completed"
        trace.completed_at = run.completed_at
    add_internal_event(
        db,
        event_type="workflow.run_completed",
        tenant_id=workflow.tenant_id,
        space_id=workflow.space_id,
        actor_type="workflow",
        actor_id=workflow.actor_id,
        trace_id=run.trace_id,
        idempotency_key=f"workflow.run_completed:{run.id}",
        payload={"workflow_id": str(workflow.id), "run_id": str(run.id)},
    )
    await db.commit()
    return run


async def execute_due_runs_once(db: AsyncSession, *, limit: int = 20) -> int:
    now = utcnow()
    run_ids = list(
        (
            await db.scalars(
                select(WorkflowRun.id)
                .where(
                    WorkflowRun.status.in_(("pending", "waiting")),
                    WorkflowRun.available_at <= now,
                )
                .order_by(WorkflowRun.available_at, WorkflowRun.created_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        ).all()
    )
    processed = 0
    for run_id in run_ids:
        run = await db.get(WorkflowRun, run_id)
        if run is None or (run.status == "waiting" and run.state.get("wait_kind") == "approval"):
            continue
        try:
            await execute_workflow_run(db, run)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await db.rollback()
            failed = await db.get(WorkflowRun, run_id)
            if failed is not None and failed.status not in {"completed", "cancelled"}:
                failed.status = "failed"
                failed.completed_at = utcnow()
                failed.error = {
                    "code": exc.code if isinstance(exc, DomainError) else "workflow_action_failed",
                    "message": (
                        exc.message
                        if isinstance(exc, DomainError)
                        else "Une action du workflow a échoué"
                    ),
                }
                trace = await db.get(Trace, failed.trace_id)
                if trace is not None:
                    trace.status = "failed"
                    trace.stop_reason = failed.error["code"]
                    trace.completed_at = failed.completed_at
                workflow = await db.get(Workflow, failed.workflow_id)
                if workflow is not None:
                    add_internal_event(
                        db,
                        event_type="workflow.run_failed",
                        tenant_id=failed.tenant_id,
                        space_id=failed.space_id,
                        actor_type="workflow",
                        actor_id=workflow.actor_id,
                        trace_id=failed.trace_id,
                        idempotency_key=f"workflow.run_failed:{failed.id}",
                        payload={
                            "workflow_id": str(failed.workflow_id),
                            "run_id": str(failed.id),
                            "error": failed.error,
                        },
                    )
                await db.commit()
            logger.warning("workflow.run_failed", run_id=str(run_id), error_type=type(exc).__name__)
        processed += 1
    return processed


async def list_workflow_runs(
    db: AsyncSession,
    tenant_id: UUID,
    *,
    workflow_id: UUID | None = None,
    status: str | None = None,
) -> list[WorkflowRun]:
    statement = select(WorkflowRun).where(WorkflowRun.tenant_id == tenant_id)
    if workflow_id is not None:
        statement = statement.where(WorkflowRun.workflow_id == workflow_id)
    if status is not None:
        statement = statement.where(WorkflowRun.status == status)
    return list((await db.scalars(statement.order_by(WorkflowRun.created_at.desc()))).all())


async def get_workflow_run(db: AsyncSession, tenant_id: UUID, run_id: UUID) -> WorkflowRun:
    run = await db.scalar(
        select(WorkflowRun).where(WorkflowRun.id == run_id, WorkflowRun.tenant_id == tenant_id)
    )
    if run is None:
        raise NotFoundError("workflow_run", run_id)
    return run


async def cancel_workflow_run(db: AsyncSession, principal: Principal, run_id: UUID) -> WorkflowRun:
    run = await get_workflow_run(db, principal.tenant_id, run_id)
    if run.status in {"completed", "failed", "cancelled"}:
        raise ConflictError("workflow_run_terminal", "Cette exécution est déjà terminée")
    run.status = "cancelled"
    run.cancelled_at = utcnow()
    run.completed_at = run.cancelled_at
    trace = await db.get(Trace, run.trace_id)
    if trace is not None:
        trace.status = "cancelled"
        trace.completed_at = run.cancelled_at
        trace.stop_reason = "cancelled_by_human"
    add_audit_event(
        db,
        tenant_id=run.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="workflow.run_cancelled",
        resource_type="workflow_run",
        resource_id=run.id,
        trace_id=run.trace_id,
    )
    add_internal_event(
        db,
        event_type="workflow.run_cancelled",
        tenant_id=run.tenant_id,
        space_id=run.space_id,
        actor_type="human",
        actor_id=principal.actor_id,
        trace_id=run.trace_id,
        idempotency_key=f"workflow.run_cancelled:{run.id}",
        payload={"workflow_id": str(run.workflow_id), "run_id": str(run.id)},
    )
    await db.commit()
    return run


async def resume_workflow_run(
    db: AsyncSession,
    principal: Principal,
    run_id: UUID,
    data: WorkflowRunResume,
) -> WorkflowRun:
    run = await get_workflow_run(db, principal.tenant_id, run_id)
    if run.status != "waiting" or run.state.get("wait_kind") != "approval":
        raise ConflictError("workflow_run_not_waiting", "Aucune approbation n’est en attente")
    state = dict(run.state)
    approval = dict(state.get("approval", {}))
    approval.update(
        {
            "status": "approved" if data.decision == "approve" else "denied",
            "decided_by_actor_id": str(principal.actor_id),
            "reason": data.reason,
            "decided_at": utcnow().isoformat(),
        }
    )
    state["approval"] = approval
    state.pop("wait_kind", None)
    run.state = state
    if data.decision == "deny":
        run.status = "cancelled"
        run.cancelled_at = utcnow()
        run.completed_at = run.cancelled_at
        trace = await db.get(Trace, run.trace_id)
        if trace is not None:
            trace.status = "cancelled"
            trace.stop_reason = "workflow_approval_denied"
            trace.completed_at = run.completed_at
    else:
        run.status = "pending"
        run.available_at = utcnow()
    add_audit_event(
        db,
        tenant_id=run.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action=f"workflow.approval_{approval['status']}",
        resource_type="workflow_run",
        resource_id=run.id,
        trace_id=run.trace_id,
    )
    add_internal_event(
        db,
        event_type="workflow.approval_decided",
        tenant_id=run.tenant_id,
        space_id=run.space_id,
        actor_type="human",
        actor_id=principal.actor_id,
        trace_id=run.trace_id,
        idempotency_key=f"workflow.approval_decided:{run.id}",
        payload={
            "workflow_id": str(run.workflow_id),
            "run_id": str(run.id),
            "decision": data.decision,
        },
    )
    await db.commit()
    return run


def verify_incoming_webhook(workflow: Workflow, raw_body: bytes, signature: str | None) -> None:
    if len(raw_body) > MAX_WEBHOOK_REQUEST_BYTES:
        raise DomainError(
            "webhook_too_large", "Le webhook dépasse la taille maximale", status_code=413
        )
    if workflow.trigger_type != "webhook" or workflow.webhook_secret_ref is None:
        raise NotFoundError("webhook", workflow.id)
    config = WebhookTrigger.model_validate(workflow.trigger_config)
    if len(raw_body) > config.max_body_bytes:
        raise DomainError(
            "webhook_too_large", "Le webhook dépasse la taille configurée", status_code=413
        )
    secret = os.environ.get(workflow.webhook_secret_ref)
    if not secret:
        raise DomainError(
            "webhook_secret_unavailable",
            "Le secret du webhook n’est pas configuré",
            status_code=503,
        )
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if signature is None or not hmac.compare_digest(signature, expected):
        raise DomainError(
            "invalid_webhook_signature", "Signature de webhook invalide", status_code=401
        )


class WorkflowEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def poll_once(self) -> int:
        async with self.session_factory() as db:
            event_runs = await trigger_event_workflows_once(db)
        async with self.session_factory() as db:
            scheduled_runs = await trigger_schedules_once(db)
        async with self.session_factory() as db:
            executed = await execute_due_runs_once(db)
        return event_runs + scheduled_runs + executed

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                count = await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("workflow.poll_failed")
                count = 0
            if count == 0:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=0.5)
                except TimeoutError:
                    pass
