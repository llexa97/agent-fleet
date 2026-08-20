from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from services.worker.adapters.base import SessionSpec
from services.worker.adapters.fake import FakeAcpAdapter
from services.worker.config import HarnessConfig


def _adapter() -> FakeAcpAdapter:
    return FakeAcpAdapter(
        HarnessConfig(
            executable=Path(sys.executable),
            args=("-m", "services.worker.fake_acp"),
            version_args=("-m", "services.worker.fake_acp", "--version"),
            prompt_timeout_seconds=5,
        )
    )


@pytest.mark.asyncio
async def test_worker_fake_acp_negotiates_streams_permissions_and_lists_sessions(
    tmp_path: Path,
) -> None:
    updates: list[tuple[str, dict[str, Any]]] = []
    permission_calls = 0

    async def update(_session: str, kind: str, content: dict[str, Any]) -> None:
        updates.append((kind, content))

    async def permission(
        _session: str, _tool: dict[str, Any], _options: list[dict[str, Any]]
    ) -> str | None:
        nonlocal permission_calls
        permission_calls += 1
        return "once"

    adapter = _adapter()
    discovery = await adapter.discover()
    assert discovery.available is True
    assert discovery.version == "agent-fleet-fake-acp 0.1.0"
    handle = await adapter.spawn(update, permission)
    try:
        capabilities = await adapter.initialize(handle)
        assert capabilities.resume_session is False
        assert capabilities.load_session is True
        assert capabilities.list_sessions is True
        session_id = await adapter.create_session(handle, SessionSpec(cwd=tmp_path))
        response = await adapter.prompt(handle, "[[permission]]")
        assert response["stop_reason"] == "end_turn"
        assert permission_calls == 1
        assert session_id in await adapter.list_sessions(handle, tmp_path)
        assert any(kind == "agent_message_chunk" for kind, _ in updates)
        assert any(kind in {"tool_call", "terminal_output"} for kind, _ in updates)
    finally:
        await adapter.terminate(handle)


@pytest.mark.asyncio
async def test_worker_fake_acp_timeout_is_really_cancelled(tmp_path: Path) -> None:
    async def update(_session: str, _kind: str, _content: dict[str, Any]) -> None:
        return None

    async def permission(
        _session: str, _tool: dict[str, Any], _options: list[dict[str, Any]]
    ) -> str | None:
        return None

    adapter = _adapter()
    handle = await adapter.spawn(update, permission)
    try:
        await adapter.initialize(handle)
        await adapter.create_session(handle, SessionSpec(cwd=tmp_path))
        prompt = asyncio.create_task(adapter.prompt(handle, "[[timeout]]"))
        await asyncio.sleep(0.05)
        assert prompt.done() is False
        await adapter.cancel(handle)
        response = await asyncio.wait_for(prompt, timeout=2)
        assert response["stop_reason"] == "cancelled"
    finally:
        await adapter.terminate(handle)
