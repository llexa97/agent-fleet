from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_collaboration import (
    AgentChannelMembership,
    Channel,
    ChannelMember,
)
from apps.api.agent_fleet_api.models_identity import (
    Actor,
    Agent,
    AgentPermission,
    AgentRuntimeBinding,
    Space,
)
from apps.api.agent_fleet_api.models_infrastructure import Worker, Workspace
from apps.api.agent_fleet_api.schemas import AgentCreate, AgentMembershipCreate, AgentUpdate
from apps.api.agent_fleet_api.security import Principal
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from packages.shared.errors import ConflictError, NotFoundError

DEFAULT_PERMISSIONS = {
    "shell": "ask",
    "filesystem_write": "ask",
    "git_commit": "allow",
    "git_push": "deny",
    "production": "deny",
    "network_sensitive": "ask",
}


async def _validate_runtime_scope(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    space_id: UUID,
    worker_id: UUID | None,
    workspace_id: UUID | None,
) -> None:
    if worker_id is not None:
        worker = await db.scalar(
            select(Worker).where(Worker.id == worker_id, Worker.tenant_id == tenant_id)
        )
        if worker is None or worker.revoked_at is not None:
            raise NotFoundError("worker", worker_id)
    if workspace_id is not None:
        workspace = await db.scalar(
            select(Workspace).where(
                Workspace.id == workspace_id,
                Workspace.tenant_id == tenant_id,
                Workspace.space_id == space_id,
            )
        )
        if workspace is None:
            raise NotFoundError("workspace", workspace_id)
        if worker_id is not None and workspace.worker_id != worker_id:
            raise ConflictError(
                "workspace_worker_mismatch", "Le workspace n’appartient pas au worker choisi"
            )


async def create_agent(db: AsyncSession, principal: Principal, data: AgentCreate) -> Agent:
    space = await db.scalar(
        select(Space).where(Space.id == data.space_id, Space.tenant_id == principal.tenant_id)
    )
    if space is None:
        raise NotFoundError("space", data.space_id)
    existing = await db.scalar(
        select(Agent.id).where(
            Agent.tenant_id == principal.tenant_id,
            Agent.space_id == data.space_id,
            Agent.handle == data.handle,
        )
    )
    if existing is not None:
        raise ConflictError("agent_handle_exists", "Ce handle existe déjà dans cet espace")
    await _validate_runtime_scope(
        db,
        tenant_id=principal.tenant_id,
        space_id=data.space_id,
        worker_id=data.runtime.worker_id,
        workspace_id=data.runtime.workspace_id,
    )
    actor = Actor(
        tenant_id=principal.tenant_id,
        space_id=data.space_id,
        actor_type="agent",
        display_name=data.display_name.strip(),
    )
    db.add(actor)
    await db.flush()
    agent = Agent(
        tenant_id=principal.tenant_id,
        space_id=data.space_id,
        actor_id=actor.id,
        handle=data.handle,
        display_name=data.display_name.strip(),
        role=data.role.strip(),
        instructions=data.instructions,
        status="active",
        max_concurrency=data.max_concurrency,
        budget_policy=data.budget_policy,
        delegation_policy=data.delegation_policy,
        tools=[
            "fleet.list_agents",
            "fleet.get_agent",
            "fleet.list_channel_members",
            "fleet.read_channel_history",
            "fleet.get_thread",
            "fleet.post_message",
            "fleet.reply_message",
            "fleet.mention_agent",
            "fleet.create_task",
            "fleet.delegate_task",
            "fleet.update_task",
            "fleet.complete_task",
            "fleet.fail_task",
            "fleet.request_human_approval",
            "fleet.get_trace",
            "fleet.cancel_trace",
        ],
    )
    db.add(agent)
    await db.flush()
    binding = AgentRuntimeBinding(
        tenant_id=principal.tenant_id,
        agent_id=agent.id,
        harness=data.runtime.harness.value,
        worker_id=data.runtime.worker_id,
        workspace_id=data.runtime.workspace_id,
        model=data.runtime.model,
        runner_selector={"labels": data.runtime.runner_labels},
        enabled=True,
    )
    db.add(binding)
    for capability, policy in DEFAULT_PERMISSIONS.items():
        db.add(
            AgentPermission(
                tenant_id=principal.tenant_id,
                agent_id=agent.id,
                capability=capability,
                policy=policy,
            )
        )
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="agent.created",
        resource_type="agent",
        resource_id=agent.id,
        details={"handle": agent.handle, "harness": binding.harness},
    )
    add_internal_event(
        db,
        event_type="agent.created",
        tenant_id=principal.tenant_id,
        space_id=agent.space_id,
        actor_type="human",
        actor_id=principal.actor_id,
        idempotency_key=f"agent.created:{agent.id}",
        payload={"agent_id": str(agent.id), "handle": agent.handle},
    )
    await db.commit()
    return agent


async def get_agent(db: AsyncSession, tenant_id: UUID, agent_id: UUID) -> Agent:
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    if agent is None:
        raise NotFoundError("agent", agent_id)
    return agent


async def list_agents(
    db: AsyncSession, principal: Principal, *, space_id: UUID | None = None
) -> list[Agent]:
    statement = select(Agent).where(Agent.tenant_id == principal.tenant_id).order_by(Agent.handle)
    if space_id is not None:
        statement = statement.where(Agent.space_id == space_id)
    return list((await db.scalars(statement)).all())


async def update_agent(
    db: AsyncSession, principal: Principal, agent_id: UUID, data: AgentUpdate
) -> Agent:
    agent = await get_agent(db, principal.tenant_id, agent_id)
    changes = data.model_dump(exclude_unset=True)
    runtime = changes.pop("runtime", None)
    for field, value in changes.items():
        setattr(agent, field, value)
    if "display_name" in changes:
        actor = await db.get(Actor, agent.actor_id)
        if actor is not None:
            actor.display_name = agent.display_name
    if runtime is not None:
        await _validate_runtime_scope(
            db,
            tenant_id=principal.tenant_id,
            space_id=agent.space_id,
            worker_id=runtime.get("worker_id"),
            workspace_id=runtime.get("workspace_id"),
        )
        old_binding = await db.scalar(
            select(AgentRuntimeBinding).where(
                AgentRuntimeBinding.agent_id == agent.id,
                AgentRuntimeBinding.enabled.is_(True),
            )
        )
        if old_binding is not None:
            old_binding.enabled = False
        db.add(
            AgentRuntimeBinding(
                tenant_id=principal.tenant_id,
                agent_id=agent.id,
                harness=runtime["harness"].value
                if hasattr(runtime["harness"], "value")
                else runtime["harness"],
                worker_id=runtime.get("worker_id"),
                workspace_id=runtime.get("workspace_id"),
                model=runtime.get("model"),
                runner_selector={"labels": runtime.get("runner_labels", [])},
                enabled=True,
            )
        )
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="agent.updated",
        resource_type="agent",
        resource_id=agent.id,
        details={"fields": sorted(changes)},
    )
    await db.commit()
    return agent


async def add_agent_to_channel(
    db: AsyncSession,
    principal: Principal,
    agent_id: UUID,
    data: AgentMembershipCreate,
) -> AgentChannelMembership:
    agent = await get_agent(db, principal.tenant_id, agent_id)
    channel = await db.scalar(
        select(Channel).where(
            Channel.id == data.channel_id,
            Channel.tenant_id == principal.tenant_id,
            Channel.space_id == agent.space_id,
        )
    )
    if channel is None:
        raise NotFoundError("channel", data.channel_id)
    if await db.scalar(
        select(AgentChannelMembership.id).where(
            AgentChannelMembership.channel_id == channel.id,
            AgentChannelMembership.agent_id == agent.id,
        )
    ):
        raise ConflictError("membership_exists", "Cet agent appartient déjà au channel")
    db.add(
        ChannelMember(
            tenant_id=principal.tenant_id,
            space_id=agent.space_id,
            channel_id=channel.id,
            actor_id=agent.actor_id,
            role="member",
        )
    )
    membership = AgentChannelMembership(
        tenant_id=principal.tenant_id,
        space_id=agent.space_id,
        channel_id=channel.id,
        agent_id=agent.id,
        activation_modes=[mode.value for mode in data.activation_modes],
    )
    db.add(membership)
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="agent.membership_created",
        resource_type="agent",
        resource_id=agent.id,
        details={"channel_id": str(channel.id)},
    )
    await db.commit()
    return membership
