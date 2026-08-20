"""Implémentation du côté client ACP utilisée par tous les harness."""

from __future__ import annotations

from typing import Any

from acp.exceptions import RequestError
from acp.interfaces import Agent
from acp.schema import (
    AllowedOutcome,
    CreateElicitationResponse,
    CreateTerminalResponse,
    DeniedOutcome,
    ElicitationMode,
    EnvVariable,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    TerminalOutputResponse,
    ToolCallUpdate,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)
from pydantic import BaseModel

from .base import PermissionHandler, UpdateHandler


class FleetAcpClient:
    """Pont ACP strict : mises à jour observables et approbations humaines."""

    def __init__(
        self, update_handler: UpdateHandler, permission_handler: PermissionHandler
    ) -> None:
        self._update_handler = update_handler
        self._permission_handler = permission_handler
        self._connection: Agent | None = None

    def on_connect(self, conn: Agent) -> None:
        self._connection = conn

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        parsed = self.classify_update(update)
        if parsed is not None:
            update_type, content = parsed
            await self._update_handler(session_id, update_type, content)

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        option_id = await self._permission_handler(
            session_id,
            tool_call.model_dump(mode="json", by_alias=False, exclude_none=True),
            [item.model_dump(mode="json", by_alias=False, exclude_none=True) for item in options],
        )
        if option_id is None or option_id not in {item.option_id for item in options}:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(
            outcome=AllowedOutcome(outcome="selected", option_id=option_id)
        )

    @staticmethod
    def classify_update(update: Any) -> tuple[str, dict[str, Any]] | None:
        if not isinstance(update, BaseModel):
            return None
        name = str(getattr(update, "session_update", "status"))
        # Une pensée ACP brute peut contenir une chaîne de raisonnement privée : on ne
        # transmet que l'existence de l'activité, jamais son contenu.
        if name == "agent_thought_chunk":
            return "status", {"status": "reasoning"}
        content = update.model_dump(mode="json", by_alias=False, exclude_none=True)
        if name == "agent_message_chunk":
            return "agent_message_chunk", content
        if name in {"plan", "plan_update", "plan_content_update", "plan_removed"}:
            return "plan", content
        if name in {"tool_call", "tool_call_update"}:
            kind = content.get("kind")
            if kind == "edit":
                return "file_change", content
            if kind == "execute":
                return "terminal_output", content
            return "tool_call", content
        if name == "usage_update":
            return "usage", content
        return "status", content

    @staticmethod
    def _unsupported(method: str) -> RequestError:
        return RequestError.method_not_found(method)

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kwargs: Any
    ) -> WriteTextFileResponse | None:
        raise self._unsupported("fs/write_text_file")

    async def read_text_file(
        self,
        session_id: str,
        path: str,
        line: int | None = None,
        limit: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        raise self._unsupported("fs/read_text_file")

    async def create_terminal(
        self,
        session_id: str,
        command: str,
        args: list[str] | None = None,
        env: list[EnvVariable] | None = None,
        cwd: str | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse:
        raise self._unsupported("terminal/create")

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> TerminalOutputResponse:
        raise self._unsupported("terminal/output")

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> ReleaseTerminalResponse | None:
        raise self._unsupported("terminal/release")

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> WaitForTerminalExitResponse:
        raise self._unsupported("terminal/wait_for_exit")

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kwargs: Any
    ) -> KillTerminalResponse | None:
        raise self._unsupported("terminal/kill")

    async def create_elicitation(
        self, message: str, mode: ElicitationMode, **kwargs: Any
    ) -> CreateElicitationResponse:
        raise self._unsupported("elicitation/create")

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        return None

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise self._unsupported(f"_{method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None
