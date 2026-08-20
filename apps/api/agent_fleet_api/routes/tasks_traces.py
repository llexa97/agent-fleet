from uuid import UUID

from fastapi import APIRouter, Header, Query, status

from apps.api.agent_fleet_api.dependencies import CurrentPrincipal, Database, MutatingPrincipal
from apps.api.agent_fleet_api.models_collaboration import Task, Trace
from apps.api.agent_fleet_api.schemas import (
    TaskCreate,
    TaskResponse,
    TaskUpdate,
    TraceResponse,
)
from apps.api.agent_fleet_api.services.task_service import (
    create_task,
    get_task,
    list_tasks,
    update_task,
)
from apps.api.agent_fleet_api.services.trace_service import (
    get_trace,
    list_traces,
    transition_trace,
)

router = APIRouter(tags=["tasks", "traces"])


@router.get("/tasks", response_model=list[TaskResponse])
async def tasks_route(
    db: Database,
    principal: CurrentPrincipal,
    space_id: UUID | None = Query(default=None),
    task_status: str | None = Query(default=None, alias="status"),
) -> list[Task]:
    return await list_tasks(
        db, tenant_id=principal.tenant_id, space_id=space_id, status=task_status
    )


@router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task_route(
    body: TaskCreate,
    db: Database,
    principal: MutatingPrincipal,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> object:
    return await create_task(
        db,
        principal=principal,
        actor_type="human",
        actor_id=principal.actor_id,
        tenant_id=principal.tenant_id,
        data=body,
        idempotency_key=idempotency_key,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task_route(task_id: UUID, db: Database, principal: CurrentPrincipal) -> object:
    return await get_task(db, principal.tenant_id, task_id)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
async def update_task_route(
    task_id: UUID, body: TaskUpdate, db: Database, principal: MutatingPrincipal
) -> object:
    return await update_task(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        task_id=task_id,
        data=body,
    )


@router.get("/traces", response_model=list[TraceResponse])
async def traces_route(
    db: Database,
    principal: CurrentPrincipal,
    space_id: UUID | None = Query(default=None),
) -> list[Trace]:
    return await list_traces(db, principal, space_id=space_id)


@router.get("/traces/{trace_id}", response_model=TraceResponse)
async def get_trace_route(trace_id: UUID, db: Database, principal: CurrentPrincipal) -> object:
    return await get_trace(db, principal.tenant_id, trace_id)


@router.post("/traces/{trace_id}/pause", response_model=TraceResponse)
async def pause_trace_route(trace_id: UUID, db: Database, principal: MutatingPrincipal) -> object:
    return await transition_trace(db, principal, trace_id, "pause")


@router.post("/traces/{trace_id}/resume", response_model=TraceResponse)
async def resume_trace_route(trace_id: UUID, db: Database, principal: MutatingPrincipal) -> object:
    return await transition_trace(db, principal, trace_id, "resume")


@router.post("/traces/{trace_id}/cancel", response_model=TraceResponse)
async def cancel_trace_route(trace_id: UUID, db: Database, principal: MutatingPrincipal) -> object:
    return await transition_trace(db, principal, trace_id, "cancel")
