import asyncio
from uuid import UUID, uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select

from apps.api.agent_fleet_api.config import get_settings
from apps.api.agent_fleet_api.database import SessionFactory
from apps.api.agent_fleet_api.models_infrastructure import Worker, WorkerCommand
from apps.api.agent_fleet_api.realtime import hub
from apps.api.agent_fleet_api.services.audit import add_internal_event
from apps.api.agent_fleet_api.services.worker_event_service import WorkerEventService
from apps.api.agent_fleet_api.services.worker_service import (
    apply_inventory,
    authenticate_worker,
)
from packages.contracts.worker_protocol import (
    PROTOCOL_VERSION,
    ControlMessageType,
    HelloPayload,
    WireEnvelope,
    WorkerInventory,
    WorkerMessageType,
    new_envelope,
)
from packages.shared.errors import DomainError
from packages.shared.time import utcnow

router = APIRouter(tags=["workers"])


def _bearer(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


async def _send(connection: object, envelope: WireEnvelope) -> None:
    async with connection.send_lock:  # type: ignore[attr-defined]
        await connection.websocket.send_text(envelope.model_dump_json())  # type: ignore[attr-defined]


async def _flush_existing_commands(worker: Worker, connection: object) -> None:
    async with SessionFactory() as db:
        commands = list(
            (
                await db.scalars(
                    select(WorkerCommand)
                    .where(
                        WorkerCommand.worker_id == worker.id,
                        WorkerCommand.status.in_(["pending", "sent"]),
                    )
                    .order_by(WorkerCommand.created_at)
                    .limit(100)
                )
            ).all()
        )
        for command in commands:
            try:
                envelope = new_envelope(
                    message_type=ControlMessageType(command.command_type),
                    worker_id=worker.id,
                    command_id=command.id,
                    trace_id=command.trace_id,
                    session_id=command.session_id,
                    idempotency_key=command.idempotency_key,
                    payload=command.payload,
                )
                await _send(connection, envelope)
                command.status = "sent"
                command.sent_at = utcnow()
                command.attempt_count += 1
            except Exception:
                break
        await db.commit()


@router.websocket("/workers/connect")
async def worker_websocket(websocket: WebSocket) -> None:
    settings = get_settings()
    raw_worker_id = websocket.query_params.get("worker_id")
    token = _bearer(websocket)
    try:
        worker_id = UUID(raw_worker_id or "")
    except ValueError:
        await websocket.close(code=4400, reason="worker_id invalide")
        return
    if token is None:
        await websocket.close(code=4401, reason="Credential worker requis")
        return
    async with SessionFactory() as db:
        authenticated_worker = await authenticate_worker(db, worker_id, token, settings)
        if authenticated_worker is None:
            await websocket.close(code=4401, reason="Credential worker invalide ou révoqué")
            return
        await db.commit()
    await websocket.accept()

    connection = None
    worker: Worker | None = None
    boot_id: UUID | None = None
    try:
        raw_hello = await asyncio.wait_for(websocket.receive_text(), timeout=5)
        if len(raw_hello.encode()) > settings.max_ws_message_bytes:
            await websocket.close(code=1009, reason="Message trop volumineux")
            return
        hello_envelope = WireEnvelope.model_validate_json(raw_hello)
        if hello_envelope.message_type != WorkerMessageType.HELLO:
            await websocket.close(code=4400, reason="hello attendu")
            return
        if hello_envelope.worker_id != worker_id:
            await websocket.close(code=4403, reason="Identité worker incohérente")
            return
        hello = HelloPayload.model_validate(hello_envelope.payload)
        boot_id = hello.boot_id
        if PROTOCOL_VERSION not in hello.supported_protocol_versions:
            await websocket.close(code=4406, reason="Version protocole incompatible")
            return
        async with SessionFactory() as db:
            worker = await db.get(Worker, worker_id)
            if worker is None or worker.revoked_at is not None:
                await websocket.close(code=4401, reason="Worker révoqué")
                return
            await apply_inventory(db, worker, hello.inventory, boot_id=hello.boot_id)
        connection = await hub.add_worker(worker_id, websocket, hello.boot_id)
        await _send(
            connection,
            new_envelope(
                message_type=ControlMessageType.WELCOME,
                worker_id=worker_id,
                command_id=hello_envelope.command_id,
                idempotency_key=f"welcome:{hello.boot_id}",
                payload={
                    "selected_protocol_version": PROTOCOL_VERSION,
                    "heartbeat_interval_seconds": 15,
                    "worker_offline_after_seconds": settings.worker_offline_after_seconds,
                    "max_message_bytes": settings.max_ws_message_bytes,
                },
            ),
        )
        await _send(
            connection,
            new_envelope(
                message_type=ControlMessageType.SYNC_CONFIGURATION,
                worker_id=worker_id,
                idempotency_key=f"sync:{hello.boot_id}",
                payload={
                    "worker_id": str(worker_id),
                    "acp_v2_experimental": False,
                    "allowed_harnesses_are_worker_local": True,
                },
            ),
        )
        await _flush_existing_commands(worker, connection)
        event_service = WorkerEventService(settings)
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode()) > settings.max_ws_message_bytes:
                await websocket.close(code=1009, reason="Message trop volumineux")
                break
            try:
                envelope = WireEnvelope.model_validate_json(raw)
                if envelope.worker_id != worker_id:
                    raise DomainError(
                        "worker_identity_mismatch", "worker_id incohérent", status_code=403
                    )
                if envelope.message_type == WorkerMessageType.INVENTORY:
                    inventory = WorkerInventory.model_validate(envelope.payload)
                    async with SessionFactory() as db:
                        current = await db.get(Worker, worker_id)
                        if current is None or current.revoked_at is not None:
                            await websocket.close(code=4401, reason="Worker révoqué")
                            break
                        await apply_inventory(db, current, inventory, boot_id=hello.boot_id)
                    ack_status = "accepted"
                else:
                    async with SessionFactory() as db:
                        current = await db.get(Worker, worker_id)
                        if current is None or current.revoked_at is not None:
                            await websocket.close(code=4401, reason="Worker révoqué")
                            break
                        ack_status = await event_service.handle(db, current, envelope)
                await _send(
                    connection,
                    new_envelope(
                        message_type=ControlMessageType.EVENT_ACK,
                        worker_id=worker_id,
                        command_id=envelope.command_id,
                        trace_id=envelope.trace_id,
                        session_id=envelope.session_id,
                        idempotency_key=f"event_ack:{envelope.message_id}",
                        payload={
                            "received_message_id": str(envelope.message_id),
                            "status": ack_status,
                        },
                    ),
                )
            except (ValidationError, ValueError, DomainError) as exc:
                code = exc.code if isinstance(exc, DomainError) else "invalid_worker_message"
                await _send(
                    connection,
                    new_envelope(
                        message_type=ControlMessageType.EVENT_ACK,
                        worker_id=worker_id,
                        command_id=uuid4(),
                        idempotency_key=f"event_ack:rejected:{uuid4()}",
                        payload={"status": "rejected", "error": {"code": code}},
                    ),
                )
    except (WebSocketDisconnect, TimeoutError):
        pass
    except ValidationError:
        await websocket.close(code=4400, reason="Message hello invalide")
    finally:
        removed = await hub.remove_worker(worker_id, websocket)
        if removed:
            async with SessionFactory() as db:
                current = await db.get(Worker, worker_id)
                if current is not None and current.revoked_at is None:
                    current.status = "offline"
                    current.disconnected_at = utcnow()
                    add_internal_event(
                        db,
                        event_type="worker.disconnected",
                        tenant_id=current.tenant_id,
                        actor_type="system",
                        actor_id=None,
                        idempotency_key=f"worker.disconnected:{worker_id}:{boot_id or 'unknown'}",
                        payload={"worker_id": str(worker_id)},
                    )
                    await db.commit()
