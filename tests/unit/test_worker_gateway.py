from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import pytest

from packages.contracts.enums import HarnessType
from packages.contracts.worker_protocol import (
    ControlMessageType,
    WorkerCapacity,
    WorkerInventory,
    WorkerMessageType,
    new_envelope,
)
from services.worker.config import (
    ControlPlaneConfig,
    HarnessConfig,
    WorkerConfig,
    WorkerIdentityConfig,
    WorkspaceConfig,
)
from services.worker.gateway import WorkerGateway
from services.worker.journal import WorkerJournal


def _config(tmp_path: Path) -> WorkerConfig:
    return WorkerConfig(
        worker=WorkerIdentityConfig(id=uuid4(), state_dir=tmp_path / "state"),
        control_plane=ControlPlaneConfig(
            url="ws://api:8000/api/v1/workers/connect",
            allowed_insecure_hosts=("api",),
        ),
        harnesses={
            HarnessType.FAKE: HarnessConfig(
                executable=Path(sys.executable),
                args=("-m", "services.worker.fake_acp"),
            )
        },
        workspaces=(WorkspaceConfig(id="project", display_name="Project", root=tmp_path),),
    )


@pytest.mark.asyncio
async def test_worker_gateway_deduplicates_control_commands_and_consumes_event_ack(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    calls = 0

    async def handler(_envelope: object) -> list[object]:
        nonlocal calls
        calls += 1
        return []

    async def inventory() -> WorkerInventory:
        return WorkerInventory(
            worker_id=config.worker.id,
            hostname="worker-test",
            version="0.1.0",
            capacity=WorkerCapacity(max_sessions=1, available_sessions=1),
        )

    async with WorkerJournal(config.journal_path) as journal:
        gateway = WorkerGateway(config, journal, handler, inventory)  # type: ignore[arg-type]
        command = new_envelope(
            message_type=ControlMessageType.SYNC_CONFIGURATION,
            worker_id=config.worker.id,
            idempotency_key=f"sync:{uuid4()}",
            payload={"acp_v2_experimental": False},
        )
        await gateway._process_incoming(command)
        await gateway._process_incoming(command)
        assert calls == 1

        event = new_envelope(
            message_type=WorkerMessageType.LOG,
            worker_id=config.worker.id,
            idempotency_key=f"log:{uuid4()}",
            payload={"message": "test"},
        )
        await gateway.emit(event)
        assert [item.message_id for item in await journal.pending()] == [event.message_id]
        event_ack = new_envelope(
            message_type=ControlMessageType.EVENT_ACK,
            worker_id=config.worker.id,
            command_id=event.command_id,
            idempotency_key=f"event-ack:{event.message_id}",
            payload={"received_message_id": str(event.message_id), "status": "accepted"},
        )
        await gateway._process_incoming(event_ack)
        assert await journal.pending() == []


def test_worker_gateway_appends_worker_id_query_parameter(tmp_path: Path) -> None:
    config = _config(tmp_path)

    async def handler(_envelope: object) -> list[object]:
        return []

    async def inventory() -> WorkerInventory:
        raise AssertionError("non appelé")

    gateway = WorkerGateway(config, WorkerJournal(config.journal_path), handler, inventory)  # type: ignore[arg-type]
    assert f"worker_id={config.worker.id}" in gateway._connection_url()
