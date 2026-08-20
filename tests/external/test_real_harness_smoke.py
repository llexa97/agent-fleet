from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from services.worker.adapters.base import HarnessAdapter, SessionSpec
from services.worker.adapters.claude import ClaudeAcpAdapter
from services.worker.adapters.codex import CodexAcpAdapter
from services.worker.config import HarnessConfig

pytestmark = pytest.mark.external


async def _run_smoke(adapter: HarnessAdapter, workspace: Path) -> None:
    updates: list[str] = []

    async def update(_session_id: str, update_type: str, _payload: dict[str, Any]) -> None:
        updates.append(update_type)

    async def deny_permission(
        _session_id: str,
        _tool_call: dict[str, Any],
        _options: list[dict[str, Any]],
    ) -> str | None:
        return None

    discovery = await adapter.discover()
    assert discovery.available, discovery.error
    handle = await adapter.spawn(update, deny_permission)
    try:
        await adapter.initialize(handle)
        await adapter.create_session(handle, SessionSpec(cwd=workspace))
        result = await adapter.prompt(
            handle,
            "Réponds uniquement par les deux mots : smoke réussi. N'utilise aucun outil.",
        )
        assert result.get("stop_reason")
        assert "agent_message_chunk" in updates
    finally:
        await adapter.terminate(handle)


def _executable(env_name: str, command: str) -> Path | None:
    configured = os.getenv(env_name)
    resolved = configured or shutil.which(command)
    return Path(resolved).resolve() if resolved else None


@pytest.mark.asyncio
async def test_real_codex_acp_smoke(tmp_path: Path) -> None:
    if os.getenv("AGENT_FLEET_RUN_CODEX_SMOKE") != "1":
        pytest.skip("activer explicitement avec AGENT_FLEET_RUN_CODEX_SMOKE=1")
    executable = _executable("AGENT_FLEET_CODEX_ACP_EXECUTABLE", "codex-acp")
    if executable is None:
        pytest.skip("codex-acp n'est pas installé")
    adapter = CodexAcpAdapter(
        HarnessConfig(
            executable=executable,
            enabled=True,
            max_instances=1,
            env_allowlist=("OPENAI_API_KEY", "CODEX_API_KEY", "NO_BROWSER"),
            startup_timeout_seconds=30,
            prompt_timeout_seconds=120,
        )
    )
    await _run_smoke(adapter, tmp_path)


@pytest.mark.asyncio
async def test_real_claude_acp_smoke(tmp_path: Path) -> None:
    if os.getenv("AGENT_FLEET_RUN_CLAUDE_SMOKE") != "1":
        pytest.skip("activer explicitement avec AGENT_FLEET_RUN_CLAUDE_SMOKE=1")
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY n'est pas défini")
    executable = _executable("AGENT_FLEET_CLAUDE_ACP_EXECUTABLE", "claude-agent-acp")
    if executable is None:
        pytest.skip("claude-agent-acp n'est pas installé")
    adapter = ClaudeAcpAdapter(
        HarnessConfig(
            executable=executable,
            enabled=True,
            max_instances=1,
            env_allowlist=("ANTHROPIC_API_KEY",),
            startup_timeout_seconds=30,
            prompt_timeout_seconds=120,
        )
    )
    await _run_smoke(adapter, tmp_path)
