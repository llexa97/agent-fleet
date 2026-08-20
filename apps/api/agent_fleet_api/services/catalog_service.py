from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.models_collaboration import Channel, ChannelMember
from apps.api.agent_fleet_api.models_identity import Space
from apps.api.agent_fleet_api.schemas import ChannelCreate, SpaceCreate
from apps.api.agent_fleet_api.security import Principal
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from packages.shared.errors import ConflictError, NotFoundError


async def list_spaces(db: AsyncSession, principal: Principal) -> list[Space]:
    return list(
        (
            await db.scalars(
                select(Space)
                .where(Space.tenant_id == principal.tenant_id)
                .order_by(Space.created_at, Space.name)
            )
        ).all()
    )


async def create_space(db: AsyncSession, principal: Principal, data: SpaceCreate) -> Space:
    existing = await db.scalar(
        select(Space.id).where(
            Space.tenant_id == principal.tenant_id,
            Space.slug == data.slug,
        )
    )
    if existing is not None:
        raise ConflictError("space_slug_exists", "Ce slug d’espace existe déjà")
    space = Space(
        tenant_id=principal.tenant_id,
        name=data.name.strip(),
        slug=data.slug,
        kind=data.kind.value,
        description=data.description,
    )
    db.add(space)
    await db.flush()
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="space.created",
        resource_type="space",
        resource_id=space.id,
    )
    add_internal_event(
        db,
        event_type="space.created",
        tenant_id=principal.tenant_id,
        space_id=space.id,
        actor_type="human",
        actor_id=principal.actor_id,
        idempotency_key=f"space.created:{space.id}",
        payload={"space_id": str(space.id), "slug": space.slug},
    )
    await db.commit()
    return space


async def get_space(db: AsyncSession, tenant_id: UUID, space_id: UUID) -> Space:
    space = await db.scalar(select(Space).where(Space.id == space_id, Space.tenant_id == tenant_id))
    if space is None:
        raise NotFoundError("space", space_id)
    return space


async def list_channels(
    db: AsyncSession,
    principal: Principal,
    *,
    space_id: UUID | None = None,
) -> list[Channel]:
    statement = (
        select(Channel)
        .join(
            ChannelMember,
            (ChannelMember.channel_id == Channel.id)
            & (ChannelMember.tenant_id == principal.tenant_id),
        )
        .where(
            Channel.tenant_id == principal.tenant_id,
            ChannelMember.actor_id == principal.actor_id,
            Channel.is_archived.is_(False),
        )
        .order_by(Channel.name)
    )
    if space_id is not None:
        statement = statement.where(Channel.space_id == space_id)
    return list((await db.scalars(statement)).unique().all())


async def create_channel(db: AsyncSession, principal: Principal, data: ChannelCreate) -> Channel:
    await get_space(db, principal.tenant_id, data.space_id)
    existing = await db.scalar(
        select(Channel.id).where(
            Channel.space_id == data.space_id,
            Channel.slug == data.slug,
        )
    )
    if existing is not None:
        raise ConflictError("channel_slug_exists", "Ce slug de channel existe déjà")
    channel = Channel(
        tenant_id=principal.tenant_id,
        space_id=data.space_id,
        name=data.name.strip(),
        slug=data.slug,
        kind=data.kind.value,
        description=data.description,
    )
    db.add(channel)
    await db.flush()
    db.add(
        ChannelMember(
            tenant_id=principal.tenant_id,
            space_id=data.space_id,
            channel_id=channel.id,
            actor_id=principal.actor_id,
            role="owner",
        )
    )
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="channel.created",
        resource_type="channel",
        resource_id=channel.id,
    )
    add_internal_event(
        db,
        event_type="channel.created",
        tenant_id=principal.tenant_id,
        space_id=channel.space_id,
        channel_id=channel.id,
        actor_type="human",
        actor_id=principal.actor_id,
        idempotency_key=f"channel.created:{channel.id}",
        payload={"channel_id": str(channel.id), "slug": channel.slug},
    )
    await db.commit()
    return channel


async def require_channel_membership(
    db: AsyncSession,
    *,
    tenant_id: UUID,
    channel_id: UUID,
    actor_id: UUID,
) -> tuple[Channel, ChannelMember]:
    row = (
        await db.execute(
            select(Channel, ChannelMember)
            .join(
                ChannelMember,
                (ChannelMember.channel_id == Channel.id)
                & (ChannelMember.tenant_id == Channel.tenant_id),
            )
            .where(
                Channel.id == channel_id,
                Channel.tenant_id == tenant_id,
                ChannelMember.actor_id == actor_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("channel", channel_id)
    return row[0], row[1]
