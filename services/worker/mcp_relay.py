"""Relais MCP local authentifié, liant l'identité à la session ACP."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import secrets
import stat
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from services.fleet_mcp_proxy.relay import RelayRequest, RelayResponse

from .errors import RelayError

ALLOWED_FLEET_TOOLS = frozenset(
    {
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
)
_FORBIDDEN_IDENTITY_FIELDS = {
    "actor_id",
    "actor_type",
    "caller_agent_id",
    "tenant_id",
    "space_id",
    "session_identity",
}


class SessionIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_session_id: UUID
    acp_session_id: str = Field(min_length=1, max_length=255)
    tenant_id: UUID
    space_id: UUID
    agent_id: UUID
    channel_id: UUID | None = None
    task_id: UUID | None = None
    trace_id: UUID

    @model_validator(mode="after")
    def channel_or_task_required(self) -> SessionIdentity:
        if self.channel_id is None and self.task_id is None:
            raise ValueError("une session MCP doit être liée à un channel ou une tâche")
        return self


@dataclass(frozen=True, slots=True)
class _TokenBinding:
    identity: SessionIdentity
    expires_at: float


ToolRelayHandler = Callable[[SessionIdentity, str, dict[str, Any], UUID], Awaitable[Any]]


class LocalMcpRelayServer:
    def __init__(
        self,
        socket_path: Path,
        handler: ToolRelayHandler,
        *,
        max_request_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if not socket_path.is_absolute():
            raise ValueError("le socket MCP doit utiliser un chemin absolu")
        self.socket_path = socket_path
        self._handler = handler
        self._max_request_bytes = max_request_bytes
        self._bindings: dict[str, _TokenBinding] = {}
        self._session_tokens: dict[UUID, set[str]] = {}
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.socket_path.exists() or self.socket_path.is_symlink():
            mode = self.socket_path.lstat().st_mode
            if not stat.S_ISSOCK(mode):
                raise RelayError("le chemin du socket MCP existe et n'est pas un socket")
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._serve_client,
            path=self.socket_path,
            limit=self._max_request_bytes + 1,
        )
        self.socket_path.chmod(0o600)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()
        self._bindings.clear()
        self._session_tokens.clear()

    def issue_token(self, identity: SessionIdentity, ttl_seconds: int) -> SecretStr:
        token = secrets.token_urlsafe(48)
        digest = self._digest(token)
        self._bindings[digest] = _TokenBinding(
            identity=identity,
            expires_at=time.monotonic() + ttl_seconds,
        )
        self._session_tokens.setdefault(identity.logical_session_id, set()).add(digest)
        return SecretStr(token)

    def revoke_session(self, logical_session_id: UUID) -> None:
        for digest in self._session_tokens.pop(logical_session_id, set()):
            self._bindings.pop(digest, None)

    async def _serve_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_id = UUID(int=0)
        try:
            line = await reader.readline()
            if not line or len(line) > self._max_request_bytes or not line.endswith(b"\n"):
                raise RelayError("requête MCP locale invalide ou trop volumineuse")
            request = RelayRequest.model_validate_json(line)
            request_id = request.request_id
            identity = self._authenticate(request.token)
            if request.tool not in ALLOWED_FLEET_TOOLS:
                raise RelayError("outil MCP non autorisé")
            if _FORBIDDEN_IDENTITY_FIELDS.intersection(request.arguments):
                raise RelayError("l'identité de l'appelant ne peut pas être fournie en paramètre")
            result = await self._handler(
                identity,
                request.tool,
                request.arguments,
                request.request_id,
            )
            response = RelayResponse(request_id=request.request_id, ok=True, result=result)
        except Exception as exc:
            response = RelayResponse(
                request_id=request_id,
                ok=False,
                error={
                    "code": "relay_denied" if isinstance(exc, RelayError) else "relay_failed",
                    "message": (
                        str(exc)[:500]
                        if isinstance(exc, RelayError)
                        else "appel fleet.* impossible"
                    ),
                },
            )
        writer.write(response.model_dump_json().encode() + b"\n")
        with contextlib.suppress(ConnectionError):
            await writer.drain()
        writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()

    def _authenticate(self, token: SecretStr) -> SessionIdentity:
        value = token.get_secret_value()
        if len(value) < 32:
            raise RelayError("jeton MCP invalide")
        digest = self._digest(value)
        binding = self._bindings.get(digest)
        if binding is None:
            raise RelayError("jeton MCP inconnu ou révoqué")
        if binding.expires_at <= time.monotonic():
            self._bindings.pop(digest, None)
            self._session_tokens.get(binding.identity.logical_session_id, set()).discard(digest)
            raise RelayError("jeton MCP expiré")
        return binding.identity

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    async def __aenter__(self) -> LocalMcpRelayServer:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()
