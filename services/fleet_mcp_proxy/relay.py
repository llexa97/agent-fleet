"""Client minimal du socket Unix privé du worker."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class RelayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID = Field(default_factory=uuid4)
    token: SecretStr
    tool: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any]


class RelayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    ok: bool
    result: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    error: dict[str, Any] | None = None


class UnixRelayClient:
    def __init__(
        self,
        socket_path: Path,
        token: SecretStr,
        *,
        timeout_seconds: float = 60,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        if not socket_path.is_absolute():
            raise ValueError("AGENT_FLEET_MCP_SOCKET doit être un chemin absolu")
        if len(token.get_secret_value()) < 32:
            raise ValueError("le jeton MCP de session est invalide")
        self._socket_path = socket_path
        self._token = token
        self._timeout = timeout_seconds
        self._max_response_bytes = max_response_bytes

    @classmethod
    def from_environment(cls) -> UnixRelayClient:
        socket_value = os.environ.get("AGENT_FLEET_MCP_SOCKET", "")
        token_value = os.environ.get("AGENT_FLEET_MCP_TOKEN", "")
        timeout = float(os.environ.get("AGENT_FLEET_MCP_TIMEOUT_SECONDS", "60"))
        return cls(Path(socket_value), SecretStr(token_value), timeout_seconds=timeout)

    async def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        request = RelayRequest(token=self._token, tool=tool, arguments=arguments)

        async def exchange() -> RelayResponse:
            reader, writer = await asyncio.open_unix_connection(self._socket_path)
            try:
                body = request.model_dump_json(context={"include_secrets": True})
                # SecretStr reste masqué à la sérialisation ; injecter explicitement la
                # valeur uniquement sur ce canal Unix local protégé.
                raw = json.loads(body)
                raw["token"] = self._token.get_secret_value()
                encoded = json.dumps(raw, separators=(",", ":")).encode() + b"\n"
                writer.write(encoded)
                await writer.drain()
                line = await reader.readline()
                if len(line) > self._max_response_bytes:
                    raise RuntimeError("réponse du worker trop volumineuse")
                return RelayResponse.model_validate_json(line)
            finally:
                writer.close()
                await writer.wait_closed()

        response = await asyncio.wait_for(exchange(), timeout=self._timeout)
        if response.request_id != request.request_id:
            raise RuntimeError("identifiant de réponse du relais incohérent")
        if not response.ok:
            message = "appel fleet.* refusé"
            if response.error and isinstance(response.error.get("message"), str):
                message = str(response.error["message"])
            raise RuntimeError(message)
        return response.result
