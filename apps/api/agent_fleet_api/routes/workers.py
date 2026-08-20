from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from apps.api.agent_fleet_api.dependencies import (
    Config,
    CurrentPrincipal,
    Database,
    MutatingPrincipal,
)
from apps.api.agent_fleet_api.models_infrastructure import Worker, WorkerHarness, Workspace
from apps.api.agent_fleet_api.schemas import (
    WorkerRegister,
    WorkerRegisterResponse,
    WorkerResponse,
    WorkspaceAssign,
    WorkspaceResponse,
)
from apps.api.agent_fleet_api.services.worker_service import (
    assign_workspace_space,
    register_worker,
    revoke_worker,
    rotate_worker_credential,
)

router = APIRouter(tags=["workers", "workspaces"])


async def _serialize_worker(db: Database, worker: Worker) -> WorkerResponse:
    harnesses = list(
        (await db.scalars(select(WorkerHarness).where(WorkerHarness.worker_id == worker.id))).all()
    )
    workspaces = list(
        (await db.scalars(select(Workspace).where(Workspace.worker_id == worker.id))).all()
    )
    return WorkerResponse(
        id=worker.id,
        tenant_id=worker.tenant_id,
        name=worker.name,
        hostname=worker.hostname,
        version=worker.version,
        protocol_version=worker.protocol_version,
        status=worker.status,
        labels=worker.labels,
        max_sessions=worker.max_sessions,
        available_sessions=worker.available_sessions,
        active_sessions=worker.active_sessions,
        last_heartbeat_at=worker.last_heartbeat_at,
        connected_at=worker.connected_at,
        revoked_at=worker.revoked_at,
        harnesses=[
            {
                "type": item.harness_type,
                "adapter": item.adapter,
                "version": item.version,
                "available": item.available,
                "capabilities": item.capabilities,
            }
            for item in harnesses
        ],
        workspaces=[
            {
                "id": str(item.id),
                "external_id": item.external_id,
                "display_name": item.display_name,
                "space_id": str(item.space_id) if item.space_id else None,
                "read_only": item.read_only,
                "status": item.status,
            }
            for item in workspaces
        ],
    )


@router.get("/workers", response_model=list[WorkerResponse])
async def workers_route(db: Database, principal: CurrentPrincipal) -> list[WorkerResponse]:
    workers = list(
        (
            await db.scalars(
                select(Worker).where(Worker.tenant_id == principal.tenant_id).order_by(Worker.name)
            )
        ).all()
    )
    return [await _serialize_worker(db, worker) for worker in workers]


@router.post("/workers", response_model=WorkerRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_worker_route(
    body: WorkerRegister,
    db: Database,
    settings: Config,
    principal: MutatingPrincipal,
) -> WorkerRegisterResponse:
    worker, credential, token = await register_worker(db, principal, body, settings)
    return WorkerRegisterResponse(
        id=worker.id,
        name=worker.name,
        token=token,
        token_hint=credential.token_hint,
    )


@router.post("/workers/{worker_id}/rotate", response_model=WorkerRegisterResponse)
async def rotate_worker_route(
    worker_id: UUID,
    db: Database,
    settings: Config,
    principal: MutatingPrincipal,
) -> WorkerRegisterResponse:
    credential, token = await rotate_worker_credential(db, principal, worker_id, settings)
    worker = await db.get(Worker, worker_id)
    assert worker is not None
    return WorkerRegisterResponse(
        id=worker.id,
        name=worker.name,
        token=token,
        token_hint=credential.token_hint,
    )


@router.post("/workers/{worker_id}/revoke", response_model=WorkerResponse)
async def revoke_worker_route(
    worker_id: UUID, db: Database, principal: MutatingPrincipal
) -> WorkerResponse:
    return await _serialize_worker(db, await revoke_worker(db, principal, worker_id))


@router.get("/workspaces", response_model=list[WorkspaceResponse])
async def workspaces_route(
    db: Database,
    principal: CurrentPrincipal,
    worker_id: UUID | None = Query(default=None),
    space_id: UUID | None = Query(default=None),
) -> list[object]:
    statement = select(Workspace).where(Workspace.tenant_id == principal.tenant_id)
    if worker_id is not None:
        statement = statement.where(Workspace.worker_id == worker_id)
    if space_id is not None:
        statement = statement.where(Workspace.space_id == space_id)
    return list((await db.scalars(statement.order_by(Workspace.display_name))).all())


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def assign_workspace_route(
    workspace_id: UUID,
    body: WorkspaceAssign,
    db: Database,
    principal: MutatingPrincipal,
) -> object:
    return await assign_workspace_space(db, principal, workspace_id, body.space_id)
