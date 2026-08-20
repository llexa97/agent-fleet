from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from apps.api.agent_fleet_api.dependencies import CurrentPrincipal, Database, MutatingPrincipal
from apps.api.agent_fleet_api.models_execution import (
    AgentSession,
    PermissionRequest,
    SessionEvent,
)
from apps.api.agent_fleet_api.schemas import (
    PermissionDecisionInput,
    PermissionRequestResponse,
    SessionEventResponse,
    SessionResponse,
)
from apps.api.agent_fleet_api.services.permission_service import decide_permission
from packages.shared.errors import NotFoundError

router = APIRouter(tags=["sessions", "permissions"])


@router.get("/sessions", response_model=list[SessionResponse])
async def sessions_route(
    db: Database,
    principal: CurrentPrincipal,
    trace_id: UUID | None = Query(default=None),
    agent_id: UUID | None = Query(default=None),
) -> list[object]:
    statement = select(AgentSession).where(AgentSession.tenant_id == principal.tenant_id)
    if trace_id is not None:
        statement = statement.where(AgentSession.trace_id == trace_id)
    if agent_id is not None:
        statement = statement.where(AgentSession.agent_id == agent_id)
    return list((await db.scalars(statement.order_by(AgentSession.created_at.desc()))).all())


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def session_route(session_id: UUID, db: Database, principal: CurrentPrincipal) -> object:
    session = await db.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.tenant_id == principal.tenant_id,
        )
    )
    if session is None:
        raise NotFoundError("session", session_id)
    return session


@router.get("/sessions/{session_id}/events", response_model=list[SessionEventResponse])
async def session_events_route(
    session_id: UUID,
    db: Database,
    principal: CurrentPrincipal,
    after_sequence: int = Query(default=-1, ge=-1),
) -> list[object]:
    if (
        await db.scalar(
            select(AgentSession.id).where(
                AgentSession.id == session_id,
                AgentSession.tenant_id == principal.tenant_id,
            )
        )
        is None
    ):
        raise NotFoundError("session", session_id)
    return list(
        (
            await db.scalars(
                select(SessionEvent)
                .where(
                    SessionEvent.session_id == session_id,
                    SessionEvent.tenant_id == principal.tenant_id,
                    SessionEvent.sequence > after_sequence,
                    SessionEvent.visible_to_user.is_(True),
                )
                .order_by(SessionEvent.sequence)
            )
        ).all()
    )


@router.get("/permissions", response_model=list[PermissionRequestResponse])
async def permissions_route(
    db: Database,
    principal: CurrentPrincipal,
    permission_status: str | None = Query(default="pending", alias="status"),
) -> list[object]:
    statement = select(PermissionRequest).where(PermissionRequest.tenant_id == principal.tenant_id)
    if permission_status:
        statement = statement.where(PermissionRequest.status == permission_status)
    return list((await db.scalars(statement.order_by(PermissionRequest.created_at.desc()))).all())


@router.post("/permissions/{request_id}/decide", response_model=PermissionRequestResponse)
async def permission_decide_route(
    request_id: UUID,
    body: PermissionDecisionInput,
    db: Database,
    principal: MutatingPrincipal,
) -> object:
    return await decide_permission(db, principal, request_id, body)
