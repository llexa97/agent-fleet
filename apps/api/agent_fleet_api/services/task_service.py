from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_collaboration import (
    AgentChannelMembership,
    Channel,
    Task,
    TaskEvent,
    Trace,
)
from apps.api.agent_fleet_api.models_identity import Agent, Space
from apps.api.agent_fleet_api.models_infrastructure import Workspace
from apps.api.agent_fleet_api.schemas import TaskCreate, TaskUpdate
from apps.api.agent_fleet_api.security import Principal
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from apps.api.agent_fleet_api.services.orchestration_policy import DEFAULT_TRACE_POLICY
from packages.shared.errors import DomainError, NotFoundError
from packages.shared.time import utcnow

ALLOWED_TASK_TRANSITIONS: dict[str, set[str]] = {
    "backlog": {"queued", "cancelled"},
    "queued": {"running", "blocked", "cancelled"},
    "running": {
        "waiting_input",
        "waiting_approval",
        "blocked",
        "review",
        "completed",
        "failed",
        "cancelled",
    },
    "waiting_input": {"running", "blocked", "cancelled"},
    "waiting_approval": {"running", "review", "cancelled"},
    "blocked": {"queued", "running", "cancelled"},
    "review": {"running", "completed", "failed", "cancelled"},
    "completed": set(),
    "failed": {"queued", "cancelled"},
    "cancelled": set(),
}


async def _validate_task_scope(db: AsyncSession, tenant_id: UUID, data: TaskCreate) -> None:
    if (
        await db.scalar(
            select(Space.id).where(Space.id == data.space_id, Space.tenant_id == tenant_id)
        )
        is None
    ):
        raise NotFoundError("space", data.space_id)
    if data.channel_id is not None:
        channel = await db.scalar(
            select(Channel).where(
                Channel.id == data.channel_id,
                Channel.tenant_id == tenant_id,
                Channel.space_id == data.space_id,
            )
        )
        if channel is None:
            raise NotFoundError("channel", data.channel_id)
    if data.assigned_agent_id is not None:
        agent = await db.scalar(
            select(Agent).where(
                Agent.id == data.assigned_agent_id,
                Agent.tenant_id == tenant_id,
                Agent.space_id == data.space_id,
            )
        )
        if agent is None:
            raise NotFoundError("agent", data.assigned_agent_id)
        if (
            data.channel_id is not None
            and await db.scalar(
                select(AgentChannelMembership.id).where(
                    AgentChannelMembership.agent_id == agent.id,
                    AgentChannelMembership.channel_id == data.channel_id,
                )
            )
            is None
        ):
            raise DomainError(
                "assignee_not_channel_member",
                "L’agent assigné n’est pas membre du channel",
                status_code=403,
            )
    if data.workspace_id is not None:
        workspace = await db.scalar(
            select(Workspace).where(
                Workspace.id == data.workspace_id,
                Workspace.tenant_id == tenant_id,
                Workspace.space_id == data.space_id,
            )
        )
        if workspace is None:
            raise NotFoundError("workspace", data.workspace_id)


async def create_task(
    db: AsyncSession,
    *,
    principal: Principal | None,
    actor_type: str,
    actor_id: UUID,
    tenant_id: UUID,
    data: TaskCreate,
    idempotency_key: str,
    requester_agent_id: UUID | None = None,
    commit: bool = True,
) -> Task:
    await _validate_task_scope(db, tenant_id, data)
    existing = await db.scalar(
        select(Task).where(
            Task.tenant_id == tenant_id,
            Task.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    trace_id = data.trace_id
    if trace_id is None:
        trace = Trace(
            tenant_id=tenant_id,
            space_id=data.space_id,
            channel_id=data.channel_id,
            thread_id=data.thread_id,
            initiator_actor_id=actor_id,
            status="running",
            policy=dict(DEFAULT_TRACE_POLICY),
        )
        db.add(trace)
        await db.flush()
        trace_id = trace.id
    task = Task(
        tenant_id=tenant_id,
        space_id=data.space_id,
        channel_id=data.channel_id,
        thread_id=data.thread_id,
        trace_id=trace_id,
        parent_task_id=data.parent_task_id,
        created_by_actor_id=actor_id,
        requester_agent_id=requester_agent_id,
        assigned_agent_id=data.assigned_agent_id,
        title=data.title.strip(),
        description=data.description,
        status="queued" if data.assigned_agent_id else "backlog",
        priority=data.priority,
        workspace_id=data.workspace_id,
        expected_artifacts=data.expected_artifacts,
        idempotency_key=idempotency_key,
        deadline=data.deadline,
    )
    db.add(task)
    await db.flush()
    db.add(
        TaskEvent(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type="task.created",
            new_status=task.status,
            payload={
                "assigned_agent_id": str(task.assigned_agent_id) if task.assigned_agent_id else None
            },
            created_at=utcnow(),
        )
    )
    add_internal_event(
        db,
        event_type="task.created",
        tenant_id=tenant_id,
        space_id=task.space_id,
        channel_id=task.channel_id,
        actor_type=actor_type,
        actor_id=actor_id,
        trace_id=trace_id,
        idempotency_key=f"task.created:{task.id}",
        payload={"task_id": str(task.id), "status": task.status},
    )
    add_audit_event(
        db,
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="task.created",
        resource_type="task",
        resource_id=task.id,
        trace_id=trace_id,
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return task


async def list_tasks(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    space_id: UUID | None = None,
    status: str | None = None,
) -> list[Task]:
    statement = select(Task).where(Task.tenant_id == tenant_id)
    if space_id is not None:
        statement = statement.where(Task.space_id == space_id)
    if status is not None:
        statement = statement.where(Task.status == status)
    return list((await db.scalars(statement.order_by(Task.priority, Task.created_at))).all())


async def get_task(db: AsyncSession, tenant_id: UUID, task_id: UUID) -> Task:
    task = await db.scalar(select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id))
    if task is None:
        raise NotFoundError("task", task_id)
    return task


async def update_task(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    actor_type: str,
    actor_id: UUID,
    task_id: UUID,
    data: TaskUpdate,
    commit: bool = True,
) -> Task:
    task = await get_task(db, tenant_id, task_id)
    old_status = task.status
    changes = data.model_dump(exclude_unset=True)
    new_status_value = changes.pop("status", None)
    new_status = new_status_value.value if hasattr(new_status_value, "value") else new_status_value
    if "assigned_agent_id" in changes and changes["assigned_agent_id"] is not None:
        assigned_agent_id = changes["assigned_agent_id"]
        assignee = await db.scalar(
            select(Agent).where(
                Agent.id == assigned_agent_id,
                Agent.tenant_id == tenant_id,
                Agent.space_id == task.space_id,
            )
        )
        if assignee is None:
            raise NotFoundError("agent", assigned_agent_id)
        if (
            task.channel_id is not None
            and await db.scalar(
                select(AgentChannelMembership.id).where(
                    AgentChannelMembership.agent_id == assignee.id,
                    AgentChannelMembership.channel_id == task.channel_id,
                )
            )
            is None
        ):
            raise DomainError(
                "assignee_not_channel_member",
                "L’agent assigné n’est pas membre du channel",
                status_code=403,
            )
    if new_status is not None and new_status != old_status:
        if new_status not in ALLOWED_TASK_TRANSITIONS.get(old_status, set()):
            raise DomainError(
                "invalid_task_transition",
                f"Transition {old_status} → {new_status} interdite",
                status_code=409,
            )
        task.status = new_status
        if new_status == "running" and task.started_at is None:
            task.started_at = utcnow()
        if new_status in {"completed", "failed", "cancelled"}:
            task.completed_at = utcnow()
    for field, value in changes.items():
        setattr(task, field, value)
    db.add(
        TaskEvent(
            tenant_id=tenant_id,
            task_id=task.id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type="task.updated",
            previous_status=old_status,
            new_status=task.status,
            payload={"fields": sorted(data.model_fields_set)},
            created_at=utcnow(),
        )
    )
    add_internal_event(
        db,
        event_type=f"task.{task.status}" if task.status != old_status else "task.updated",
        tenant_id=tenant_id,
        space_id=task.space_id,
        channel_id=task.channel_id,
        actor_type=actor_type,
        actor_id=actor_id,
        trace_id=task.trace_id,
        idempotency_key=f"task.updated:{task.id}:{uuid4()}",
        payload={"task_id": str(task.id), "previous_status": old_status, "status": task.status},
    )
    add_audit_event(
        db,
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action="task.updated",
        resource_type="task",
        resource_id=task.id,
        trace_id=task.trace_id,
        details={"previous_status": old_status, "status": task.status},
    )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return task
