from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from apps.api.agent_fleet_api.dependencies import CurrentPrincipal, Database, MutatingPrincipal
from apps.api.agent_fleet_api.models_collaboration import AgentChannelMembership
from apps.api.agent_fleet_api.models_identity import Agent, AgentRuntimeBinding
from apps.api.agent_fleet_api.schemas import (
    AgentCreate,
    AgentMembershipCreate,
    AgentResponse,
    AgentUpdate,
)
from apps.api.agent_fleet_api.services.agent_service import (
    add_agent_to_channel,
    create_agent,
    get_agent,
    list_agents,
    update_agent,
)

router = APIRouter(prefix="/agents", tags=["agents"])


async def _serialize(db: Database, agent: Agent) -> AgentResponse:
    binding = await db.scalar(
        select(AgentRuntimeBinding).where(
            AgentRuntimeBinding.agent_id == agent.id,
            AgentRuntimeBinding.enabled.is_(True),
        )
    )
    channels = list(
        (
            await db.scalars(
                select(AgentChannelMembership.channel_id).where(
                    AgentChannelMembership.agent_id == agent.id
                )
            )
        ).all()
    )
    return AgentResponse(
        id=agent.id,
        actor_id=agent.actor_id,
        tenant_id=agent.tenant_id,
        space_id=agent.space_id,
        handle=agent.handle,
        display_name=agent.display_name,
        role=agent.role,
        instructions=agent.instructions,
        status=agent.status,
        max_concurrency=agent.max_concurrency,
        budget_policy=agent.budget_policy,
        delegation_policy=agent.delegation_policy,
        harness=binding.harness if binding else None,
        worker_id=binding.worker_id if binding else None,
        workspace_id=binding.workspace_id if binding else None,
        model=binding.model if binding else None,
        channels=channels,
        created_at=agent.created_at,
    )


@router.get("", response_model=list[AgentResponse])
async def agents_route(
    db: Database,
    principal: CurrentPrincipal,
    space_id: UUID | None = Query(default=None),
) -> list[AgentResponse]:
    return [
        await _serialize(db, item) for item in await list_agents(db, principal, space_id=space_id)
    ]


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent_route(
    body: AgentCreate, db: Database, principal: MutatingPrincipal
) -> AgentResponse:
    return await _serialize(db, await create_agent(db, principal, body))


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent_route(
    agent_id: UUID, db: Database, principal: CurrentPrincipal
) -> AgentResponse:
    return await _serialize(db, await get_agent(db, principal.tenant_id, agent_id))


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent_route(
    agent_id: UUID, body: AgentUpdate, db: Database, principal: MutatingPrincipal
) -> AgentResponse:
    return await _serialize(db, await update_agent(db, principal, agent_id, body))


@router.post("/{agent_id}/memberships", status_code=status.HTTP_201_CREATED)
async def add_membership_route(
    agent_id: UUID,
    body: AgentMembershipCreate,
    db: Database,
    principal: MutatingPrincipal,
) -> dict[str, str]:
    membership = await add_agent_to_channel(db, principal, agent_id, body)
    return {"id": str(membership.id), "status": "created"}
