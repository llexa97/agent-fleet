import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, status

from apps.api.agent_fleet_api.dependencies import CurrentPrincipal, Database, MutatingPrincipal
from apps.api.agent_fleet_api.models_governance import Workflow, WorkflowRun
from apps.api.agent_fleet_api.schemas import (
    WorkflowCreate,
    WorkflowResponse,
    WorkflowRunRequest,
    WorkflowRunResponse,
    WorkflowRunResume,
    WorkflowUpdate,
)
from apps.api.agent_fleet_api.services.workflow_service import (
    cancel_workflow_run,
    create_workflow,
    create_workflow_run,
    execute_workflow_run,
    get_workflow,
    get_workflow_run,
    list_workflow_runs,
    list_workflows,
    resume_workflow_run,
    transition_workflow,
    update_workflow,
    verify_incoming_webhook,
)
from packages.shared.errors import DomainError

router = APIRouter(tags=["workflows"])


def _validate_idempotency_key(value: str) -> str:
    if not 8 <= len(value) <= 180:
        raise DomainError(
            "invalid_idempotency_key",
            "Idempotency-Key doit contenir entre 8 et 180 caractères",
            status_code=422,
        )
    return value


@router.get("/workflows", response_model=list[WorkflowResponse])
async def workflows_route(
    db: Database,
    principal: CurrentPrincipal,
    space_id: UUID | None = Query(default=None),
) -> list[Workflow]:
    return await list_workflows(db, principal.tenant_id, space_id=space_id)


@router.post("/workflows", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow_route(
    body: WorkflowCreate, db: Database, principal: MutatingPrincipal
) -> object:
    return await create_workflow(db, principal, body)


@router.get("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow_route(
    workflow_id: UUID, db: Database, principal: CurrentPrincipal
) -> object:
    return await get_workflow(db, principal.tenant_id, workflow_id)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow_route(
    workflow_id: UUID,
    body: WorkflowUpdate,
    db: Database,
    principal: MutatingPrincipal,
) -> object:
    return await update_workflow(db, principal, workflow_id, body)


@router.post("/workflows/{workflow_id}/activate", response_model=WorkflowResponse)
async def activate_workflow_route(
    workflow_id: UUID, db: Database, principal: MutatingPrincipal
) -> object:
    return await transition_workflow(db, principal, workflow_id, "active")


@router.post("/workflows/{workflow_id}/pause", response_model=WorkflowResponse)
async def pause_workflow_route(
    workflow_id: UUID, db: Database, principal: MutatingPrincipal
) -> object:
    return await transition_workflow(db, principal, workflow_id, "paused")


@router.post("/workflows/{workflow_id}/run", response_model=WorkflowRunResponse)
async def run_workflow_route(
    workflow_id: UUID,
    body: WorkflowRunRequest,
    db: Database,
    principal: MutatingPrincipal,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> object:
    workflow = await get_workflow(db, principal.tenant_id, workflow_id)
    run = await create_workflow_run(
        db,
        workflow,
        trigger_type="manual",
        idempotency_key=f"workflow:{workflow.id}:manual:{_validate_idempotency_key(idempotency_key)}",
        trigger_payload=body.input,
        actor_id=principal.actor_id,
        actor_type="human",
    )
    return await execute_workflow_run(db, run)


@router.post("/workflows/{workflow_id}/webhook", response_model=WorkflowRunResponse)
async def incoming_webhook_route(
    workflow_id: UUID,
    request: Request,
    db: Database,
    signature: str | None = Header(default=None, alias="X-Agent-Fleet-Signature"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> object:
    workflow = await db.get(Workflow, workflow_id)
    if workflow is None or workflow.status != "active":
        # Réponse identique pour ne pas révéler un webhook inactif.
        raise DomainError("webhook_unavailable", "Webhook indisponible", status_code=404)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1_048_576:
            raise DomainError(
                "webhook_too_large", "Le webhook dépasse la taille maximale", status_code=413
            )
    raw_body = bytes(raw)
    verify_incoming_webhook(workflow, raw_body, signature)
    try:
        payload: Any = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DomainError(
            "invalid_webhook_json", "Le corps doit être un objet JSON", status_code=422
        ) from exc
    if not isinstance(payload, dict):
        raise DomainError(
            "invalid_webhook_json", "Le corps doit être un objet JSON", status_code=422
        )
    run = await create_workflow_run(
        db,
        workflow,
        trigger_type="webhook",
        idempotency_key=f"workflow:{workflow.id}:webhook:{_validate_idempotency_key(idempotency_key)}",
        trigger_payload=payload,
        actor_id=workflow.actor_id,
        actor_type="workflow",
    )
    return await execute_workflow_run(db, run)


@router.get("/workflow-runs", response_model=list[WorkflowRunResponse])
async def workflow_runs_route(
    db: Database,
    principal: CurrentPrincipal,
    workflow_id: UUID | None = Query(default=None),
    run_status: str | None = Query(default=None, alias="status"),
) -> list[WorkflowRun]:
    if run_status is not None and run_status not in {
        "pending",
        "running",
        "waiting",
        "completed",
        "failed",
        "cancelled",
    }:
        raise DomainError(
            "invalid_workflow_run_status", "Statut d’exécution invalide", status_code=422
        )
    return await list_workflow_runs(
        db, principal.tenant_id, workflow_id=workflow_id, status=run_status
    )


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run_route(run_id: UUID, db: Database, principal: CurrentPrincipal) -> object:
    return await get_workflow_run(db, principal.tenant_id, run_id)


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRunResponse)
async def cancel_workflow_run_route(
    run_id: UUID, db: Database, principal: MutatingPrincipal
) -> object:
    return await cancel_workflow_run(db, principal, run_id)


@router.post("/workflow-runs/{run_id}/resume", response_model=WorkflowRunResponse)
async def resume_workflow_run_route(
    run_id: UUID,
    body: WorkflowRunResume,
    db: Database,
    principal: MutatingPrincipal,
) -> object:
    run = await resume_workflow_run(db, principal, run_id, body)
    if run.status == "pending":
        return await execute_workflow_run(db, run)
    return run
