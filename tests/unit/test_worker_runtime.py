from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from packages.contracts.enums import HarnessType
from packages.contracts.worker_protocol import (
    ControlMessageType,
    WireEnvelope,
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
from services.worker.journal import WorkerJournal
from services.worker.mcp_relay import LocalMcpRelayServer, SessionIdentity
from services.worker.runtime import WorkerRuntime
from services.worker.workspaces import WorkspaceResolver


def _config(tmp_path: Path, state_dir: Path) -> WorkerConfig:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return WorkerConfig(
        worker=WorkerIdentityConfig(
            id=uuid4(),
            hostname="worker-runtime-test",
            state_dir=state_dir,
            max_sessions=2,
        ),
        control_plane=ControlPlaneConfig(
            url="ws://api:8000/api/v1/workers/connect",
            allowed_insecure_hosts=("api",),
        ),
        harnesses={
            HarnessType.FAKE: HarnessConfig(
                executable=Path(sys.executable),
                args=("-m", "services.worker.fake_acp"),
                version_args=("-m", "services.worker.fake_acp", "--version"),
                prompt_timeout_seconds=10,
                max_instances=2,
            )
        },
        workspaces=(WorkspaceConfig(id="project", display_name="Project", root=workspace),),
    )


def _payload(target: UUID) -> tuple[dict[str, Any], UUID, UUID, UUID]:
    tenant_id = uuid4()
    space_id = uuid4()
    channel_id = uuid4()
    agent_id = uuid4()
    trace_id = uuid4()
    task_id = uuid4()
    return (
        {
            "delivery_id": str(uuid4()),
            "generation": 1,
            "tenant_id": str(tenant_id),
            "space_id": str(space_id),
            "channel_id": str(channel_id),
            "agent_id": str(agent_id),
            "agent_actor_id": str(uuid4()),
            "harness": "fake",
            "model": None,
            "workspace_id": "project",
            "system_prompt": ("Tu es @cto.\nDemandeur : Axel\nUtilise les outils fleet.*."),
            "prompt": (
                f"Délègue ce travail [[delegate-task-if-requester:Axel:{target}]] [[complete-task]]"
            ),
            "context": {
                "agent": {"id": str(agent_id), "handle": "cto"},
                "channel": {"id": str(channel_id), "slug": "client-taxi"},
                "thread_id": None,
                "task_id": str(task_id),
                "trace_id": str(trace_id),
            },
        },
        trace_id,
        channel_id,
        agent_id,
    )


@pytest.mark.asyncio
async def test_worker_runtime_runs_fake_acp_and_structured_agent_delegation(
    tmp_path: Path,
) -> None:
    with tempfile.TemporaryDirectory(prefix="af-runtime-", dir="/tmp") as short_dir:
        config = _config(tmp_path, Path(short_dir))
        events: list[WireEnvelope] = []
        completed = asyncio.Event()
        runtime_holder: dict[str, WorkerRuntime] = {}

        async with WorkerJournal(config.journal_path) as journal:

            async def relay_handler(
                identity: SessionIdentity,
                tool: str,
                arguments: dict[str, Any],
                call_id: UUID,
            ) -> Any:
                return await runtime_holder["runtime"].relay_tool_call(
                    identity, tool, arguments, call_id
                )

            relay = LocalMcpRelayServer(config.relay_socket_path, relay_handler)
            await relay.start()

            async def emit(envelope: WireEnvelope) -> None:
                events.append(envelope)
                if envelope.message_type == WorkerMessageType.TOOL_CALL:
                    await runtime_holder["runtime"].handle(
                        new_envelope(
                            message_type=ControlMessageType.TOOL_RESULT,
                            worker_id=config.worker.id,
                            trace_id=envelope.trace_id,
                            session_id=envelope.session_id,
                            idempotency_key=f"tool-result:{envelope.payload['call_id']}",
                            payload={
                                "call_id": envelope.payload["call_id"],
                                "ok": True,
                                "result": {"message_id": str(uuid4())},
                                "error": None,
                            },
                        )
                    )
                if envelope.message_type in {
                    WorkerMessageType.SESSION_COMPLETED,
                    WorkerMessageType.SESSION_FAILED,
                }:
                    completed.set()

            runtime = WorkerRuntime(
                config,
                journal,
                WorkspaceResolver(config.workspaces),
                relay,
                emit,
            )
            runtime_holder["runtime"] = runtime
            target = uuid4()
            payload, trace_id, _, _ = _payload(target)
            logical_session_id = uuid4()
            command = new_envelope(
                message_type=ControlMessageType.START_SESSION,
                worker_id=config.worker.id,
                trace_id=trace_id,
                session_id=logical_session_id,
                idempotency_key=f"start:{payload['delivery_id']}",
                payload=payload,
            )
            try:
                await runtime.handle(command)
                await asyncio.wait_for(completed.wait(), timeout=10)
                types = [str(item.message_type) for item in events]
                failures = [
                    item.payload
                    for item in events
                    if item.message_type == WorkerMessageType.SESSION_FAILED
                ]
                assert WorkerMessageType.SESSION_FAILED not in types, repr(failures)
                assert types[0] == WorkerMessageType.SESSION_STARTED
                assert WorkerMessageType.TOOL_CALL in types
                tool_names = {
                    str(item.payload["tool_name"])
                    for item in events
                    if item.message_type == WorkerMessageType.TOOL_CALL
                }
                assert tool_names == {"fleet.delegate_task", "fleet.complete_task"}
                assert WorkerMessageType.SESSION_UPDATE in types
                completion = next(
                    item
                    for item in events
                    if item.message_type == WorkerMessageType.SESSION_COMPLETED
                )
                assert completion.payload["published_via_tool"] is True
                assert "Fake ACP a traité" in completion.payload["final_text"]
                stored = await journal.load_session(logical_session_id)
                assert stored is not None
                assert stored.state == "completed"

                # Une seconde livraison utilise le même identifiant logique après
                # libération du processus : le worker reprend la session ACP durable.
                completed.clear()
                second_start = len(events)
                second_payload = {
                    **payload,
                    "delivery_id": str(uuid4()),
                    "prompt": "Deuxième tour sans délégation",
                }
                await runtime.handle(
                    new_envelope(
                        message_type=ControlMessageType.PROMPT,
                        worker_id=config.worker.id,
                        trace_id=trace_id,
                        session_id=logical_session_id,
                        idempotency_key=f"prompt:{second_payload['delivery_id']}",
                        payload=second_payload,
                    )
                )
                await asyncio.wait_for(completed.wait(), timeout=10)
                second_types = [str(item.message_type) for item in events[second_start:]]
                assert second_types[0] == WorkerMessageType.SESSION_RESUMED
                assert WorkerMessageType.SESSION_COMPLETED in second_types
                assert WorkerMessageType.SESSION_FAILED not in second_types
            finally:
                await runtime.shutdown()
                await relay.close()
