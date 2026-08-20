from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from packages.contracts.worker_protocol import ControlMessageType, WorkerMessageType, new_envelope
from services.worker.journal import CommandClaim, StoredSession, WorkerJournal


@pytest.mark.asyncio
async def test_worker_journal_deduplicates_commands_and_persists_sessions(tmp_path: Path) -> None:
    path = tmp_path / "journal.sqlite3"
    worker_id = uuid4()
    command = new_envelope(
        message_type=ControlMessageType.START_SESSION,
        worker_id=worker_id,
        idempotency_key=f"start:{uuid4()}",
    )
    logical_id = uuid4()

    async with WorkerJournal(path) as journal:
        assert await journal.begin_command(command) is CommandClaim.NEW
        assert await journal.begin_command(command) is CommandClaim.PROCESSING
        await journal.complete_command(command.command_id, {"ok": True})
        assert await journal.begin_command(command) is CommandClaim.COMPLETED
        await journal.save_session(
            StoredSession(
                logical_session_id=logical_id,
                acp_session_id="acp-1",
                harness_type="fake",
                workspace_id="project",
                state="active",
                metadata={"sequence": 2},
            )
        )

    async with WorkerJournal(path) as journal:
        stored = await journal.load_session(logical_id)
        assert stored is not None
        assert stored.acp_session_id == "acp-1"
        assert stored.metadata == {"sequence": 2}


@pytest.mark.asyncio
async def test_worker_journal_replays_only_unacknowledged_non_rejected_events(
    tmp_path: Path,
) -> None:
    async with WorkerJournal(tmp_path / "journal.sqlite3") as journal:
        accepted = new_envelope(
            message_type=WorkerMessageType.SESSION_UPDATE,
            worker_id=uuid4(),
            idempotency_key=f"event:{uuid4()}",
        )
        rejected = accepted.model_copy(
            update={"message_id": uuid4(), "idempotency_key": f"event:{uuid4()}"}
        )
        waiting = accepted.model_copy(
            update={"message_id": uuid4(), "idempotency_key": f"event:{uuid4()}"}
        )
        for envelope in (accepted, rejected, waiting):
            await journal.enqueue(envelope)
        await journal.acknowledge(accepted.message_id)
        await journal.reject(rejected.message_id, {"code": "invalid"})

        assert [item.message_id for item in await journal.pending()] == [waiting.message_id]
