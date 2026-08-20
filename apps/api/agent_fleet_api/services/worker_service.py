from datetime import timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.agent_fleet_api.config import Settings
from apps.api.agent_fleet_api.models_identity import Space
from apps.api.agent_fleet_api.models_infrastructure import (
    Worker,
    WorkerCredential,
    WorkerHarness,
    Workspace,
)
from apps.api.agent_fleet_api.schemas import WorkerRegister
from apps.api.agent_fleet_api.security import Principal, generate_secret, hash_secret
from apps.api.agent_fleet_api.services.audit import add_audit_event, add_internal_event
from packages.contracts.worker_protocol import WorkerInventory
from packages.shared.errors import ConflictError, ForbiddenError, NotFoundError
from packages.shared.time import as_utc, utcnow


def _require_owner(principal: Principal) -> None:
    if not principal.is_owner:
        raise ForbiddenError("Seul le propriétaire peut administrer les workers")


async def register_worker(
    db: AsyncSession,
    principal: Principal,
    data: WorkerRegister,
    settings: Settings,
) -> tuple[Worker, WorkerCredential, str]:
    _require_owner(principal)
    if await db.scalar(
        select(Worker.id).where(
            Worker.tenant_id == principal.tenant_id,
            Worker.name == data.name,
        )
    ):
        raise ConflictError("worker_name_exists", "Ce nom de worker existe déjà")
    worker = Worker(
        tenant_id=principal.tenant_id,
        name=data.name,
        labels=data.labels,
        status="registered",
    )
    db.add(worker)
    await db.flush()
    token = generate_secret()
    credential = WorkerCredential(
        tenant_id=principal.tenant_id,
        worker_id=worker.id,
        token_hash=hash_secret(token, settings.session_secret),
        token_hint=token[-8:],
    )
    db.add(credential)
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="worker.registered",
        resource_type="worker",
        resource_id=worker.id,
        details={"name": worker.name},
    )
    await db.commit()
    return worker, credential, token


async def rotate_worker_credential(
    db: AsyncSession,
    principal: Principal,
    worker_id: UUID,
    settings: Settings,
) -> tuple[WorkerCredential, str]:
    _require_owner(principal)
    worker = await get_worker(db, principal.tenant_id, worker_id)
    token = generate_secret()
    current = list(
        (
            await db.scalars(
                select(WorkerCredential).where(
                    WorkerCredential.worker_id == worker.id,
                    WorkerCredential.revoked_at.is_(None),
                )
            )
        ).all()
    )
    now = utcnow()
    credential = WorkerCredential(
        tenant_id=principal.tenant_id,
        worker_id=worker.id,
        token_hash=hash_secret(token, settings.session_secret),
        token_hint=token[-8:],
        rotated_from_id=current[0].id if current else None,
    )
    db.add(credential)
    await db.flush()
    for item in current:
        item.expires_at = now + timedelta(minutes=10)
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="worker.credential_rotated",
        resource_type="worker",
        resource_id=worker.id,
    )
    await db.commit()
    return credential, token


async def revoke_worker(db: AsyncSession, principal: Principal, worker_id: UUID) -> Worker:
    _require_owner(principal)
    worker = await get_worker(db, principal.tenant_id, worker_id)
    now = utcnow()
    worker.status = "revoked"
    worker.revoked_at = now
    credentials = list(
        (
            await db.scalars(
                select(WorkerCredential).where(
                    WorkerCredential.worker_id == worker.id,
                    WorkerCredential.revoked_at.is_(None),
                )
            )
        ).all()
    )
    for item in credentials:
        item.revoked_at = now
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="worker.revoked",
        resource_type="worker",
        resource_id=worker.id,
    )
    await db.commit()
    return worker


async def authenticate_worker(
    db: AsyncSession,
    worker_id: UUID,
    token: str,
    settings: Settings,
) -> Worker | None:
    token_hash = hash_secret(token, settings.session_secret)
    row = (
        await db.execute(
            select(Worker, WorkerCredential)
            .join(WorkerCredential, WorkerCredential.worker_id == Worker.id)
            .where(
                Worker.id == worker_id,
                WorkerCredential.token_hash == token_hash,
                WorkerCredential.revoked_at.is_(None),
                Worker.revoked_at.is_(None),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    worker = cast(Worker, row[0])
    credential = cast(WorkerCredential, row[1])
    now = utcnow()
    if credential.expires_at is not None and as_utc(credential.expires_at) <= now:
        return None
    credential.last_used_at = now
    return worker


async def get_worker(db: AsyncSession, tenant_id: UUID, worker_id: UUID) -> Worker:
    worker = await db.scalar(
        select(Worker).where(Worker.id == worker_id, Worker.tenant_id == tenant_id)
    )
    if worker is None:
        raise NotFoundError("worker", worker_id)
    return worker


async def apply_inventory(
    db: AsyncSession,
    worker: Worker,
    inventory: WorkerInventory,
    *,
    boot_id: UUID,
) -> None:
    if inventory.worker_id != worker.id:
        raise ConflictError("worker_identity_mismatch", "L’inventaire usurpe un autre worker")
    now = utcnow()
    worker.hostname = inventory.hostname
    worker.version = inventory.version
    worker.protocol_version = "1.0"
    worker.boot_id = boot_id
    worker.status = "online"
    worker.labels = inventory.labels
    worker.max_sessions = inventory.capacity.max_sessions
    worker.available_sessions = inventory.capacity.available_sessions
    worker.active_sessions = inventory.capacity.max_sessions - inventory.capacity.available_sessions
    worker.last_heartbeat_at = now
    worker.connected_at = now
    worker.disconnected_at = None

    await db.execute(delete(WorkerHarness).where(WorkerHarness.worker_id == worker.id))
    for harness in inventory.harnesses:
        db.add(
            WorkerHarness(
                tenant_id=worker.tenant_id,
                worker_id=worker.id,
                harness_type=harness.type.value,
                adapter=harness.adapter,
                version=harness.version,
                available=harness.available,
                capabilities=harness.capabilities,
            )
        )
    known_workspaces = {
        item.external_id: item
        for item in (
            await db.scalars(select(Workspace).where(Workspace.worker_id == worker.id))
        ).all()
    }
    announced_ids: set[str] = set()
    for announced in inventory.workspaces:
        announced_ids.add(announced.id)
        workspace = known_workspaces.get(announced.id)
        if workspace is None:
            workspace = Workspace(
                tenant_id=worker.tenant_id,
                worker_id=worker.id,
                external_id=announced.id,
                display_name=announced.display_name,
                root=announced.root,
                canonical_root=announced.root,
                read_only=announced.read_only,
                status="available",
            )
            db.add(workspace)
        else:
            workspace.display_name = announced.display_name
            workspace.root = announced.root
            workspace.canonical_root = announced.root
            workspace.read_only = announced.read_only
            workspace.status = "available"
    for external_id, workspace in known_workspaces.items():
        if external_id not in announced_ids:
            workspace.status = "offline"
    add_internal_event(
        db,
        event_type="worker.connected",
        tenant_id=worker.tenant_id,
        actor_type="system",
        actor_id=None,
        idempotency_key=f"worker.connected:{worker.id}:{boot_id}",
        payload={"worker_id": str(worker.id), "boot_id": str(boot_id)},
    )
    await db.commit()


async def assign_workspace_space(
    db: AsyncSession,
    principal: Principal,
    workspace_id: UUID,
    space_id: UUID,
) -> Workspace:
    _require_owner(principal)
    workspace = await db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == principal.tenant_id,
        )
    )
    if workspace is None:
        raise NotFoundError("workspace", workspace_id)
    if (
        await db.scalar(
            select(Space.id).where(Space.id == space_id, Space.tenant_id == principal.tenant_id)
        )
        is None
    ):
        raise NotFoundError("space", space_id)
    workspace.space_id = space_id
    add_audit_event(
        db,
        tenant_id=principal.tenant_id,
        actor_type="human",
        actor_id=principal.actor_id,
        action="workspace.assigned",
        resource_type="workspace",
        resource_id=workspace.id,
        details={"space_id": str(space_id)},
    )
    await db.commit()
    return workspace
