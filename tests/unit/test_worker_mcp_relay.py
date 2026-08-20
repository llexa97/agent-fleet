from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import SecretStr

from services.fleet_mcp_proxy.relay import UnixRelayClient
from services.fleet_mcp_proxy.server import mcp
from services.worker.mcp_relay import LocalMcpRelayServer, SessionIdentity


def _identity() -> SessionIdentity:
    return SessionIdentity(
        logical_session_id=uuid4(),
        acp_session_id="acp-test",
        tenant_id=uuid4(),
        space_id=uuid4(),
        agent_id=uuid4(),
        channel_id=uuid4(),
        trace_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_worker_mcp_relay_binds_identity_and_rejects_impersonation(
    tmp_path: Path,
) -> None:
    received: list[tuple[SessionIdentity, str, dict[str, Any]]] = []

    async def handler(
        identity: SessionIdentity, tool: str, arguments: dict[str, Any], _call_id: Any
    ) -> Any:
        received.append((identity, tool, arguments))
        return {"ok": True}

    identity = _identity()
    # macOS borne les chemins AF_UNIX à environ 104 octets ; pytest utilise un
    # chemin volontairement descriptif et long. Le chemin de production /var/lib est court.
    with tempfile.TemporaryDirectory(prefix="af-relay-", dir="/tmp") as short_dir:
        relay = LocalMcpRelayServer(Path(short_dir) / "fleet.sock", handler)
        await relay.start()
        token = relay.issue_token(identity, ttl_seconds=60)
        client = UnixRelayClient(relay.socket_path, token)
        try:
            result = await client.call(
                "fleet.list_agents", {"channel_id": str(identity.channel_id)}
            )
            assert result == {"ok": True}
            assert received[0][0].agent_id == identity.agent_id

            with pytest.raises(RuntimeError, match="identité"):
                await client.call(
                    "fleet.list_agents",
                    {"actor_id": str(uuid4()), "channel_id": str(identity.channel_id)},
                )

            relay.revoke_session(identity.logical_session_id)
            with pytest.raises(RuntimeError, match="inconnu ou révoqué"):
                await client.call("fleet.list_agents", {"channel_id": str(identity.channel_id)})
        finally:
            await relay.close()


@pytest.mark.asyncio
async def test_worker_mcp_proxy_exposes_all_required_fleet_tools() -> None:
    tools = {tool.name for tool in await mcp.list_tools()}
    assert tools == {
        "fleet.list_agents",
        "fleet.get_agent",
        "fleet.list_channel_members",
        "fleet.read_channel_history",
        "fleet.get_thread",
        "fleet.post_message",
        "fleet.reply_message",
        "fleet.mention_agent",
        "fleet.create_task",
        "fleet.delegate_task",
        "fleet.update_task",
        "fleet.complete_task",
        "fleet.fail_task",
        "fleet.request_human_approval",
        "fleet.get_trace",
        "fleet.cancel_trace",
    }


def test_worker_mcp_client_rejects_short_session_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="jeton MCP"):
        UnixRelayClient(tmp_path / "fleet.sock", SecretStr("short"))
