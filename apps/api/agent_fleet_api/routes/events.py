from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import or_, select

from apps.api.agent_fleet_api.config import get_settings
from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.dependencies import CurrentPrincipal, Database
from apps.api.agent_fleet_api.models_governance import InternalEvent
from apps.api.agent_fleet_api.models_identity import Actor, User, UserSession
from apps.api.agent_fleet_api.realtime import hub
from apps.api.agent_fleet_api.security import hash_secret, session_cookie_name
from apps.api.agent_fleet_api.services.outbox import serialize_event
from packages.shared.time import as_utc, utcnow

router = APIRouter(tags=["events"])


async def _events_after(
    db: Database,
    *,
    tenant_id: UUID,
    after: UUID | None,
    limit: int,
) -> list[InternalEvent]:
    statement = select(InternalEvent).where(InternalEvent.tenant_id == tenant_id)
    if after is not None:
        cursor = await db.scalar(
            select(InternalEvent).where(
                InternalEvent.id == after,
                InternalEvent.tenant_id == tenant_id,
            )
        )
        if cursor is not None:
            statement = statement.where(
                or_(
                    InternalEvent.occurred_at > cursor.occurred_at,
                    (InternalEvent.occurred_at == cursor.occurred_at)
                    & (InternalEvent.id > cursor.id),
                )
            )
    return list(
        (
            await db.scalars(
                statement.order_by(InternalEvent.occurred_at, InternalEvent.id).limit(limit)
            )
        ).all()
    )


@router.get("/events")
async def events_route(
    db: Database,
    principal: CurrentPrincipal,
    after: UUID | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, object]]:
    return [
        serialize_event(item)
        for item in await _events_after(db, tenant_id=principal.tenant_id, after=after, limit=limit)
    ]


async def _websocket_tenant(websocket: WebSocket) -> UUID | None:
    settings = get_settings()
    token = websocket.cookies.get(session_cookie_name(settings))
    if not token:
        return None
    token_hash = hash_secret(token, settings.session_secret)
    async with SessionFactory() as db:
        row = (
            await db.execute(
                select(UserSession, User, Actor)
                .join(User, User.id == UserSession.user_id)
                .join(
                    Actor,
                    (Actor.user_id == User.id)
                    & (Actor.tenant_id == User.tenant_id)
                    & (Actor.actor_type == "human"),
                )
                .where(UserSession.token_hash == token_hash)
            )
        ).one_or_none()
        if row is None:
            return None
        session, user, _actor = row
        if session.revoked_at or as_utc(session.expires_at) <= utcnow() or user.status != "active":
            return None
        tenant_id: UUID = user.tenant_id
        return tenant_id


def _origin_allowed(origin: str | None) -> bool:
    settings = get_settings()
    if settings.environment == "test":
        return True
    if not origin:
        return False
    expected = {
        f"{urlsplit(settings.web_origin).scheme}://{urlsplit(settings.web_origin).netloc}",
        f"{urlsplit(settings.public_url).scheme}://{urlsplit(settings.public_url).netloc}",
    }
    return origin in expected


@router.websocket("/events/ws")
async def events_websocket(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403, reason="Origin refusée")
        return
    tenant_id = await _websocket_tenant(websocket)
    if tenant_id is None:
        await websocket.close(code=4401, reason="Session requise")
        return
    await websocket.accept()
    await hub.add_browser(tenant_id, websocket)
    try:
        raw_after = websocket.query_params.get("after")
        after = UUID(raw_after) if raw_after else None
        async with SessionFactory() as db:
            replay = await _events_after(db, tenant_id=tenant_id, after=after, limit=1000)
        for event in replay:
            await websocket.send_json(serialize_event(event))
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_text("pong")
    except (WebSocketDisconnect, ValueError):
        pass
    finally:
        await hub.remove_browser(tenant_id, websocket)
