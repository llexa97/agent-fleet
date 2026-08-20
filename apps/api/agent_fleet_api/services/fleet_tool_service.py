from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_collaboration import (
    AgentChannelMembership,
    ChannelMember,
    Task,
    Thread,
)
from apps.api.agent_fleet_api.models_execution import AgentSession, PermissionRequest
from apps.api.agent_fleet_api.models_identity import Actor, Agent
from apps.api.agent_fleet_api.schemas import (
    MentionInput,
    MessageCreate,
    TaskCreate,
    TaskUpdate,
)
from apps.api.agent_fleet_api.services.message_service import (
    list_channel_messages,
    post_message,
    serialize_message,
)
from apps.api.agent_fleet_api.services.task_service import create_task, get_task, update_task
from apps.api.agent_fleet_api.services.trace_service import get_trace
from packages.shared.errors import DomainError, ForbiddenError, NotFoundError
from packages.shared.time import utcnow

MUTATING_TOOLS = {
    "fleet.post_message",
    "fleet.reply_message",
    "fleet.mention_agent",
    "fleet.create_task",
    "fleet.delegate_task",
    "fleet.update_task",
    "fleet.complete_task",
    "fleet.fail_task",
    "fleet.request_human_approval",
    "fleet.cancel_trace",
}


class FleetToolService:
    async def execute(
        self,
        db: AsyncSession,
        *,
        session_id: UUID,
        worker_id: UUID,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: str,
    ) -> dict[str, Any]:
        session = await db.scalar(
            select(AgentSession).where(
                AgentSession.id == session_id,
                AgentSession.worker_id == worker_id,
            )
        )
        if session is None:
            raise NotFoundError("session", session_id)
        agent = await db.scalar(
            select(Agent).where(
                Agent.id == session.agent_id,
                Agent.tenant_id == session.tenant_id,
                Agent.space_id == session.space_id,
            )
        )
        if agent is None or agent.status != "active":
            raise ForbiddenError("L’identité d’agent liée à la session est inactive")
        if tool_name not in agent.tools:
            raise ForbiddenError("Cet outil n’est pas autorisé pour l’agent", code="tool_denied")
        # L'identité vient exclusivement de session.agent_id; tout champ client est rejeté.
        forbidden_identity_fields = {"caller_agent_id", "author_id", "actor_id", "tenant_id"}
        if forbidden_identity_fields.intersection(arguments):
            raise ForbiddenError("Un outil ne peut pas choisir l’identité de l’appelant")
        # Les wrappers MCP sérialisent leurs paramètres optionnels à ``null``.
        # Une valeur nulle ne doit pas masquer le channel immuable de la session.
        channel_id = UUID(str(arguments.get("channel_id") or session.channel_id))
        if (
            await db.scalar(
                select(AgentChannelMembership.id).where(
                    AgentChannelMembership.agent_id == agent.id,
                    AgentChannelMembership.channel_id == channel_id,
                )
            )
            is None
        ):
            raise ForbiddenError("L’agent n’est pas membre du channel")

        if tool_name == "fleet.list_agents":
            agents = list(
                (
                    await db.scalars(
                        select(Agent)
                        .join(
                            AgentChannelMembership,
                            (AgentChannelMembership.agent_id == Agent.id)
                            & (AgentChannelMembership.channel_id == channel_id),
                        )
                        .where(
                            Agent.tenant_id == session.tenant_id,
                            Agent.space_id == session.space_id,
                            Agent.status == "active",
                        )
                        .order_by(Agent.handle)
                    )
                ).all()
            )
            return {
                "agents": [
                    {"id": str(item.id), "handle": item.handle, "role": item.role}
                    for item in agents
                ]
            }
        if tool_name == "fleet.get_agent":
            target = await self._resolve_agent(db, session, channel_id, arguments)
            return {
                "agent": {
                    "id": str(target.id),
                    "handle": target.handle,
                    "display_name": target.display_name,
                    "role": target.role,
                    "status": target.status,
                }
            }
        if tool_name == "fleet.list_channel_members":
            rows = (
                await db.execute(
                    select(ChannelMember, Actor, Agent)
                    .join(Actor, Actor.id == ChannelMember.actor_id)
                    .outerjoin(Agent, Agent.actor_id == Actor.id)
                    .where(
                        ChannelMember.tenant_id == session.tenant_id,
                        ChannelMember.channel_id == channel_id,
                    )
                )
            ).all()
            return {
                "members": [
                    {
                        "actor_id": str(actor.id),
                        "actor_type": actor.actor_type,
                        "display_name": actor.display_name,
                        "agent_id": str(target.id) if target else None,
                        "handle": target.handle if target else None,
                    }
                    for _membership, actor, target in rows
                ]
            }
        if tool_name == "fleet.read_channel_history":
            limit = max(1, min(int(arguments.get("limit", 30)), 100))
            messages = await list_channel_messages(
                db,
                tenant_id=session.tenant_id,
                actor_id=agent.actor_id,
                channel_id=channel_id,
                thread_id=UUID(arguments["thread_id"]) if arguments.get("thread_id") else None,
                limit=limit,
            )
            return {"messages": [await serialize_message(db, item) for item in messages]}
        if tool_name == "fleet.get_thread":
            thread_id = UUID(str(arguments["thread_id"]))
            thread = await db.scalar(
                select(Thread).where(
                    Thread.id == thread_id,
                    Thread.tenant_id == session.tenant_id,
                    Thread.channel_id == channel_id,
                )
            )
            if thread is None:
                raise NotFoundError("thread", thread_id)
            return {
                "thread": {
                    "id": str(thread.id),
                    "title": thread.title,
                    "summary": thread.summary,
                    "is_closed": thread.is_closed,
                }
            }
        if tool_name in {"fleet.post_message", "fleet.reply_message"}:
            body = MessageCreate(
                content=str(arguments["content"]),
                thread_id=UUID(arguments["thread_id"])
                if arguments.get("thread_id")
                else session.thread_id,
                reply_to_id=(
                    UUID(arguments["reply_to_id"]) if arguments.get("reply_to_id") else None
                ),
                mentions=[],
                expects_response=False,
            )
            created = await post_message(
                db,
                tenant_id=session.tenant_id,
                author_actor_id=agent.actor_id,
                author_type="agent",
                channel_id=channel_id,
                data=body,
                idempotency_key=f"fleet:{call_id}",
                source_agent_id=agent.id,
                trace_id=session.trace_id,
                commit=False,
            )
            session.publication_count_current_turn += 1
            return {"message_id": str(created.message.id)}
        if tool_name == "fleet.mention_agent":
            target = await self._resolve_agent(db, session, channel_id, arguments)
            body = MessageCreate(
                content=str(arguments["content"]),
                thread_id=session.thread_id,
                mentions=[
                    MentionInput(
                        target_type="agent",
                        target_id=target.id,
                        handle_at_creation=target.handle,
                    )
                ],
                expects_response=True,
            )
            created = await post_message(
                db,
                tenant_id=session.tenant_id,
                author_actor_id=agent.actor_id,
                author_type="agent",
                channel_id=channel_id,
                data=body,
                idempotency_key=f"fleet:{call_id}",
                source_agent_id=agent.id,
                trace_id=session.trace_id,
                depth=int(arguments.get("depth", 1)),
                commit=False,
            )
            session.publication_count_current_turn += 1
            return {"message_id": str(created.message.id), "target_agent_id": str(target.id)}
        if tool_name in {"fleet.create_task", "fleet.delegate_task"}:
            assignee: Agent | None = None
            if tool_name == "fleet.delegate_task":
                assignee = await self._resolve_agent(db, session, channel_id, arguments)
                allowed = set(
                    str(item) for item in agent.delegation_policy.get("allowed_agents", [])
                )
                if assignee.handle not in allowed and str(assignee.id) not in allowed:
                    raise ForbiddenError("La politique de délégation interdit cet agent")
            task_data = TaskCreate(
                space_id=session.space_id,
                channel_id=channel_id,
                thread_id=session.thread_id,
                trace_id=session.trace_id,
                parent_task_id=UUID(arguments["parent_task_id"])
                if arguments.get("parent_task_id")
                else session.task_id,
                assigned_agent_id=assignee.id if assignee else None,
                title=str(arguments["title"]),
                description=str(arguments.get("description", "")),
                priority=int(arguments.get("priority", 2)),
                workspace_id=session.workspace_id,
                expected_artifacts=arguments.get("expected_artifacts", []),
            )
            task = await create_task(
                db,
                principal=None,
                actor_type="agent",
                actor_id=agent.actor_id,
                tenant_id=session.tenant_id,
                data=task_data,
                idempotency_key=f"fleet:{call_id}",
                requester_agent_id=agent.id,
                commit=False,
            )
            if assignee is not None:
                mention_body = MessageCreate(
                    content=f"@{assignee.handle} — {task.title}\n\n{task.description}",
                    thread_id=session.thread_id,
                    mentions=[
                        MentionInput(
                            target_type="agent",
                            target_id=assignee.id,
                            handle_at_creation=assignee.handle,
                        )
                    ],
                    expects_response=True,
                )
                await post_message(
                    db,
                    tenant_id=session.tenant_id,
                    author_actor_id=agent.actor_id,
                    author_type="agent",
                    channel_id=channel_id,
                    data=mention_body,
                    idempotency_key=f"fleet:{call_id}:delegation-message",
                    source_agent_id=agent.id,
                    trace_id=session.trace_id,
                    task_id=task.id,
                    depth=1,
                    commit=False,
                )
            session.publication_count_current_turn += 1 if assignee else 0
            return {"task_id": str(task.id), "status": task.status}
        if tool_name in {
            "fleet.update_task",
            "fleet.complete_task",
            "fleet.fail_task",
        }:
            task_id = UUID(str(arguments.get("task_id") or session.task_id))
            task = await get_task(db, session.tenant_id, task_id)
            if task.assigned_agent_id != agent.id and task.requester_agent_id != agent.id:
                raise ForbiddenError("L’agent n’est ni assigné ni demandeur de cette tâche")
            if tool_name == "fleet.complete_task":
                data = TaskUpdate(
                    status="completed",
                    result_summary=arguments.get("result_summary"),
                    result=arguments.get("result", {}),
                )
            elif tool_name == "fleet.fail_task":
                data = TaskUpdate(
                    status="failed",
                    result_summary=arguments.get("error", "Échec signalé par l’agent"),
                )
            else:
                data = TaskUpdate.model_validate(arguments.get("changes", {}))
            updated = await update_task(
                db,
                tenant_id=session.tenant_id,
                actor_type="agent",
                actor_id=agent.actor_id,
                task_id=task.id,
                data=data,
                commit=False,
            )
            await self._notify_requester_if_terminal(db, session, agent, updated, call_id)
            return {"task_id": str(updated.id), "status": updated.status}
        if tool_name == "fleet.request_human_approval":
            request = PermissionRequest(
                tenant_id=session.tenant_id,
                space_id=session.space_id,
                agent_id=agent.id,
                session_id=session.id,
                trace_id=session.trace_id,
                delivery_id=None,
                external_request_id=f"fleet:{call_id}",
                capability=str(arguments.get("capability", "external_action")),
                action_summary=str(arguments["summary"]),
                action_details=arguments.get("details", {}),
                workspace_id=session.workspace_id,
                status="pending",
            )
            db.add(request)
            await db.flush()
            return {"permission_request_id": str(request.id), "status": "pending"}
        if tool_name == "fleet.get_trace":
            trace = await get_trace(db, session.tenant_id, session.trace_id)
            return {
                "trace": {
                    "id": str(trace.id),
                    "status": trace.status,
                    "turn_count": trace.turn_count,
                    "delegation_count": trace.delegation_count,
                    "tokens": trace.token_count,
                    "cost_eur": float(trace.cost_eur),
                    "stop_reason": trace.stop_reason,
                }
            }
        if tool_name == "fleet.cancel_trace":
            if not agent.delegation_policy.get("allow_cancel_trace", False):
                raise ForbiddenError("L’agent ne peut pas annuler une trace")
            trace = await get_trace(db, session.tenant_id, session.trace_id)
            trace.status = "cancelled"
            trace.stop_reason = "cancelled_by_agent_policy"
            trace.completed_at = utcnow()
            return {"trace_id": str(trace.id), "status": trace.status}
        raise DomainError("unknown_fleet_tool", f"Outil inconnu: {tool_name}", status_code=404)

    async def _resolve_agent(
        self,
        db: AsyncSession,
        session: AgentSession,
        channel_id: UUID,
        arguments: dict[str, Any],
    ) -> Agent:
        target_id = arguments.get("agent_id") or arguments.get("target_agent_id")
        handle = arguments.get("handle") or arguments.get("target_handle")
        statement = (
            select(Agent)
            .join(
                AgentChannelMembership,
                (AgentChannelMembership.agent_id == Agent.id)
                & (AgentChannelMembership.channel_id == channel_id),
            )
            .where(
                Agent.tenant_id == session.tenant_id,
                Agent.space_id == session.space_id,
                Agent.status == "active",
            )
        )
        if target_id:
            statement = statement.where(Agent.id == UUID(str(target_id)))
        elif handle:
            statement = statement.where(Agent.handle == str(handle))
        else:
            raise DomainError(
                "agent_target_required", "agent_id ou handle est requis", status_code=422
            )
        target = await db.scalar(statement)
        if target is None:
            raise NotFoundError("agent membre du channel", target_id or handle)
        return target

    async def _notify_requester_if_terminal(
        self,
        db: AsyncSession,
        session: AgentSession,
        completing_agent: Agent,
        task: Task,
        call_id: str,
    ) -> None:
        if task.status not in {"completed", "failed"} or task.requester_agent_id is None:
            return
        requester = await db.scalar(
            select(Agent).where(
                Agent.id == task.requester_agent_id,
                Agent.tenant_id == session.tenant_id,
            )
        )
        if requester is None:
            return
        summary = task.result_summary or (
            "Tâche terminée" if task.status == "completed" else "Tâche échouée"
        )
        await post_message(
            db,
            tenant_id=session.tenant_id,
            author_actor_id=completing_agent.actor_id,
            author_type="agent",
            channel_id=task.channel_id or session.channel_id,
            data=MessageCreate(
                content=f"@{requester.handle} — {task.title}: {summary}",
                thread_id=task.thread_id,
                mentions=[
                    MentionInput(
                        target_type="agent",
                        target_id=requester.id,
                        handle_at_creation=requester.handle,
                    )
                ],
                expects_response=True,
            ),
            idempotency_key=f"fleet:{call_id}:requester-notification",
            source_agent_id=completing_agent.id,
            trace_id=task.trace_id,
            task_id=task.parent_task_id or task.id,
            depth=1,
            commit=False,
        )
        session.publication_count_current_turn += 1
