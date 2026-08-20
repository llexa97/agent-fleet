from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from apps.api.agent_fleet_api.dependencies import CurrentPrincipal, Database, MutatingPrincipal
from apps.api.agent_fleet_api.models_collaboration import Channel, ChannelMember
from apps.api.agent_fleet_api.models_identity import Actor, Agent, Space
from apps.api.agent_fleet_api.schemas import (
    ChannelCreate,
    ChannelMemberResponse,
    ChannelResponse,
    SpaceCreate,
    SpaceResponse,
)
from apps.api.agent_fleet_api.services.catalog_service import (
    create_channel,
    create_space,
    list_channels,
    list_spaces,
    require_channel_membership,
)

router = APIRouter(tags=["spaces", "channels"])


@router.get("/spaces", response_model=list[SpaceResponse])
async def spaces_route(db: Database, principal: CurrentPrincipal) -> list[Space]:
    return await list_spaces(db, principal)


@router.post("/spaces", response_model=SpaceResponse, status_code=status.HTTP_201_CREATED)
async def create_space_route(
    body: SpaceCreate, db: Database, principal: MutatingPrincipal
) -> object:
    return await create_space(db, principal, body)


@router.get("/channels", response_model=list[ChannelResponse])
async def channels_route(
    db: Database,
    principal: CurrentPrincipal,
    space_id: UUID | None = Query(default=None),
) -> list[Channel]:
    return await list_channels(db, principal, space_id=space_id)


@router.post("/channels", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_channel_route(
    body: ChannelCreate, db: Database, principal: MutatingPrincipal
) -> object:
    return await create_channel(db, principal, body)


@router.get("/channels/{channel_id}/members", response_model=list[ChannelMemberResponse])
async def channel_members_route(
    channel_id: UUID, db: Database, principal: CurrentPrincipal
) -> list[ChannelMemberResponse]:
    await require_channel_membership(
        db,
        tenant_id=principal.tenant_id,
        channel_id=channel_id,
        actor_id=principal.actor_id,
    )
    rows = (
        await db.execute(
            select(ChannelMember, Actor, Agent)
            .join(Actor, Actor.id == ChannelMember.actor_id)
            .outerjoin(Agent, Agent.actor_id == Actor.id)
            .where(
                ChannelMember.tenant_id == principal.tenant_id,
                ChannelMember.channel_id == channel_id,
            )
            .order_by(Actor.display_name)
        )
    ).all()
    return [
        ChannelMemberResponse(
            actor_id=actor.id,
            actor_type=actor.actor_type,
            display_name=actor.display_name,
            agent_id=agent.id if agent else None,
            handle=agent.handle if agent else None,
            role=membership.role,
        )
        for membership, actor, agent in rows
    ]
