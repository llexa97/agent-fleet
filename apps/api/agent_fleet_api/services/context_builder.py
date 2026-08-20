from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_collaboration import (
    AgentChannelMembership,
    Channel,
    Message,
    Task,
    Thread,
)
from apps.api.agent_fleet_api.models_execution import Delivery
from apps.api.agent_fleet_api.models_identity import (
    Actor,
    Agent,
    AgentPermission,
    AgentRuntimeBinding,
)
from apps.api.agent_fleet_api.models_infrastructure import Workspace
from packages.shared.errors import NotFoundError


@dataclass(slots=True)
class ContextBundle:
    system_prompt: str
    prompt: str
    structured: dict[str, Any]


class ContextBuilder:
    def __init__(self, *, max_recent_messages: int = 30, max_recent_characters: int = 24_000):
        self.max_recent_messages = max_recent_messages
        self.max_recent_characters = max_recent_characters

    async def build(self, db: AsyncSession, delivery: Delivery) -> ContextBundle:
        agent = await db.scalar(
            select(Agent).where(
                Agent.id == delivery.target_agent_id,
                Agent.tenant_id == delivery.tenant_id,
                Agent.space_id == delivery.space_id,
            )
        )
        message = await db.scalar(
            select(Message).where(
                Message.id == delivery.message_id,
                Message.tenant_id == delivery.tenant_id,
                Message.channel_id == delivery.channel_id,
            )
        )
        channel = await db.scalar(
            select(Channel).where(
                Channel.id == delivery.channel_id,
                Channel.tenant_id == delivery.tenant_id,
                Channel.space_id == delivery.space_id,
            )
        )
        if agent is None:
            raise NotFoundError("agent", delivery.target_agent_id)
        if message is None:
            raise NotFoundError("message", delivery.message_id)
        if channel is None:
            raise NotFoundError("channel", delivery.channel_id)

        binding = await db.scalar(
            select(AgentRuntimeBinding).where(
                AgentRuntimeBinding.agent_id == agent.id,
                AgentRuntimeBinding.enabled.is_(True),
            )
        )
        workspace = (
            await db.scalar(
                select(Workspace).where(
                    Workspace.id == binding.workspace_id,
                    Workspace.tenant_id == delivery.tenant_id,
                    Workspace.space_id == delivery.space_id,
                )
            )
            if binding is not None and binding.workspace_id is not None
            else None
        )
        task = (
            await db.scalar(
                select(Task).where(
                    Task.id == delivery.task_id,
                    Task.tenant_id == delivery.tenant_id,
                    Task.space_id == delivery.space_id,
                )
            )
            if delivery.task_id
            else None
        )
        thread = (
            await db.scalar(
                select(Thread).where(
                    Thread.id == delivery.thread_id,
                    Thread.tenant_id == delivery.tenant_id,
                    Thread.channel_id == delivery.channel_id,
                )
            )
            if delivery.thread_id
            else None
        )
        members = list(
            (
                await db.scalars(
                    select(Agent)
                    .join(
                        AgentChannelMembership,
                        (AgentChannelMembership.agent_id == Agent.id)
                        & (AgentChannelMembership.channel_id == delivery.channel_id),
                    )
                    .where(
                        Agent.tenant_id == delivery.tenant_id,
                        Agent.space_id == delivery.space_id,
                        Agent.status == "active",
                    )
                    .order_by(Agent.handle)
                )
            ).all()
        )
        permissions = list(
            (
                await db.scalars(
                    select(AgentPermission).where(AgentPermission.agent_id == agent.id)
                )
            ).all()
        )
        author = await db.get(Actor, message.author_id)

        recent_statement = select(Message).where(
            Message.tenant_id == delivery.tenant_id,
            Message.channel_id == delivery.channel_id,
            Message.created_at <= message.created_at,
        )
        if delivery.thread_id:
            recent_statement = recent_statement.where(Message.thread_id == delivery.thread_id)
        recent_desc = list(
            (
                await db.scalars(
                    recent_statement.order_by(Message.created_at.desc()).limit(
                        self.max_recent_messages
                    )
                )
            ).all()
        )
        selected: list[Message] = []
        used_characters = 0
        for recent in recent_desc:
            if selected and used_characters + len(recent.content) > self.max_recent_characters:
                break
            selected.append(recent)
            used_characters += len(recent.content)
        selected.reverse()
        history = [
            {
                "id": str(item.id),
                "author_type": item.author_type,
                "author_id": str(item.author_id),
                "content": item.content,
                "created_at": item.created_at.isoformat(),
            }
            for item in selected
        ]
        member_lines = "\n".join(f"- @{item.handle} — {item.role}" for item in members)
        permission_lines = "\n".join(f"- {item.capability}: {item.policy}" for item in permissions)
        task_block = (
            f"Tâche : {task.title} ({task.id})\nDescription : {task.description}\n" if task else ""
        )
        summary_block = f"Résumé ancien : {thread.summary}\n" if thread and thread.summary else ""
        workspace_text = workspace.external_id if workspace else "aucun workspace"
        system_prompt = (
            f"Tu es @{agent.handle}.\n"
            f"Rôle : {agent.role}\n"
            f"Instructions :\n{agent.instructions}\n\n"
            f"Channel : #{channel.slug}\n"
            f"Trace : {delivery.trace_id}\n"
            f"Demandeur : {author.display_name if author else message.author_type}\n"
            f"{task_block}{summary_block}"
            f"Membres agents disponibles :\n{member_lines or '- aucun'}\n"
            f"Workspace autorisé : {workspace_text}\n"
            f"Permissions :\n{permission_lines or '- politiques par défaut'}\n"
            "Pour communiquer ou déléguer, utilise exclusivement les outils fleet.*.\n"
            "Une citation textuelle contenant @handle n'est jamais une délégation structurée.\n"
            "N'expose aucun secret et respecte les limites de la trace."
        )
        return ContextBundle(
            system_prompt=system_prompt,
            prompt=message.content,
            structured={
                "agent": {
                    "id": str(agent.id),
                    "actor_id": str(agent.actor_id),
                    "handle": agent.handle,
                    "role": agent.role,
                },
                "channel": {"id": str(channel.id), "slug": channel.slug},
                "thread_id": str(delivery.thread_id) if delivery.thread_id else None,
                "task_id": str(task.id) if task else None,
                "trace_id": str(delivery.trace_id),
                "workspace": (
                    {
                        "id": str(workspace.id),
                        "external_id": workspace.external_id,
                        "read_only": workspace.read_only,
                    }
                    if workspace
                    else None
                ),
                "members": [
                    {"id": str(item.id), "handle": item.handle, "role": item.role}
                    for item in members
                ],
                "permissions": {item.capability: item.policy for item in permissions},
                "budget": agent.budget_policy,
                "recent_messages": history,
            },
        )
