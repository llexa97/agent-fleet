"""Agent ACP de test : streaming, MCP, permission, crash, timeout et reprise."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from acp import (
    PROTOCOL_VERSION,
    Agent,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    start_tool_call,
    text_block,
    update_agent_message,
    update_plan,
    update_tool_call,
)
from acp.exceptions import RequestError
from acp.interfaces import Client
from acp.schema import (
    AcpMcpServer,
    AgentCapabilities,
    AudioContentBlock,
    AuthenticateResponse,
    ClientCapabilities,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    ForkSessionResponse,
    HttpMcpServer,
    ImageContentBlock,
    Implementation,
    ListSessionsResponse,
    LoadSessionResponse,
    McpServerStdio,
    PermissionOption,
    PlanEntry,
    RequestPermissionResponse,
    ResourceContentBlock,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionInfo,
    SessionListCapabilities,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
    SseMcpServer,
    TextContentBlock,
    ToolCallUpdate,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

_DELEGATE_DIRECTIVE = re.compile(r"\[\[delegate:(?P<agent>[0-9a-fA-F-]{36})\]\]")
_CONDITIONAL_DELEGATE_DIRECTIVE = re.compile(
    r"\[\[delegate-if-requester:(?P<requester>[^:\]\r\n]{1,80}):"
    r"(?P<agent>[0-9a-fA-F-]{36})\]\]"
)
_DELEGATE_TASK_DIRECTIVE = re.compile(r"\[\[delegate-task:(?P<agent>[0-9a-fA-F-]{36})\]\]")
_CONDITIONAL_DELEGATE_TASK_DIRECTIVE = re.compile(
    r"\[\[delegate-task-if-requester:(?P<requester>[^:\]\r\n]{1,80}):"
    r"(?P<agent>[0-9a-fA-F-]{36})\]\]"
)
_CONDITIONAL_COMPLETE_TASK_DIRECTIVE = re.compile(
    r"\[\[complete-task-if-requester:(?P<requester>[^\]\r\n]{1,80})\]\]"
)


@dataclass(slots=True)
class _FakeSession:
    cwd: str
    mcp_servers: tuple[McpServerStdio, ...]
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    prompt_count: int = 0
    closed: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class FakeAcpAgent(Agent):
    """Comportements contrôlés par des directives explicites dans le prompt.

    ``[[permission]]`` demande une approbation, ``[[timeout]]`` attend une
    annulation, ``[[crash]]`` termine le processus et
    ``[[delegate:<uuid>]]`` appelle réellement ``fleet.mention_agent`` via MCP.
    """

    _conn: Client

    def __init__(self, *, stream_delay_seconds: float = 0.01) -> None:
        self._sessions: dict[str, _FakeSession] = {}
        self._stream_delay = stream_delay_seconds

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_info=Implementation(
                name="agent-fleet-fake-acp",
                title="Agent Fleet deterministic fake ACP",
                version="0.1.0",
            ),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                ),
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio]
        | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        session_id = uuid4().hex
        self._sessions[session_id] = _FakeSession(
            cwd=cwd,
            mcp_servers=tuple(
                item for item in (mcp_servers or []) if isinstance(item, McpServerStdio)
            ),
        )
        return NewSessionResponse(session_id=session_id)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio]
        | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        self._restore(session_id, cwd, mcp_servers)
        return LoadSessionResponse()

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio]
        | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        self._restore(session_id, cwd, mcp_servers)
        await self._conn.session_update(
            session_id=session_id,
            update=update_agent_message(text_block("Session ACP simulée reprise.")),
        )
        return ResumeSessionResponse()

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> ListSessionsResponse:
        sessions = [
            SessionInfo(
                session_id=session_id,
                cwd=session.cwd,
                title="Fake ACP session",
                updated_at=session.updated_at.isoformat(),
            )
            for session_id, session in self._sessions.items()
            if not session.closed and (cwd is None or session.cwd == cwd)
        ]
        return ListSessionsResponse(sessions=sessions)

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        session = self._sessions.get(session_id)
        if session is not None:
            session.closed = True
            session.cancel_event.set()
        return CloseSessionResponse()

    async def prompt(
        self,
        session_id: str,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        **kwargs: Any,
    ) -> PromptResponse:
        session = self._sessions[session_id]
        session.cancel_event.clear()
        session.prompt_count += 1
        session.updated_at = datetime.now(UTC)
        text = "\n".join(block.text for block in prompt if isinstance(block, TextContentBlock))
        await self._conn.session_update(
            session_id=session_id,
            update=update_plan(
                [
                    PlanEntry(
                        content="Traiter la demande simulée",
                        priority="high",
                        status="in_progress",
                    )
                ]
            ),
        )

        if "[[crash]]" in text:
            os._exit(70)
        if "[[timeout]]" in text:
            await session.cancel_event.wait()
            return PromptResponse(stop_reason="cancelled")
        if "[[permission]]" in text:
            allowed = await self._ask_permission(session_id)
            if not allowed:
                await self._stream(session_id, "Permission refusée par le contrôle humain.")
                return PromptResponse(stop_reason="refusal")

        task_delegate = self._matching_directive(
            text,
            _DELEGATE_TASK_DIRECTIVE,
            _CONDITIONAL_DELEGATE_TASK_DIRECTIVE,
        )
        mention_delegate = self._matching_directive(
            text,
            _DELEGATE_DIRECTIVE,
            _CONDITIONAL_DELEGATE_DIRECTIVE,
        )
        if task_delegate is not None:
            await self._delegate_task(session_id, session, task_delegate)
        elif mention_delegate is not None:
            await self._mention_agent(session_id, session, mention_delegate)
        conditional_completion = _CONDITIONAL_COMPLETE_TASK_DIRECTIVE.search(text)
        should_complete = "[[complete-task]]" in text or (
            conditional_completion is not None
            and f"Demandeur : {conditional_completion.group('requester').strip()}" in text
        )
        if should_complete:
            await self._complete_task(session_id, session)
        response = f"Fake ACP a traité la demande #{session.prompt_count}."
        await self._stream(session_id, response)
        await self._conn.session_update(
            session_id=session_id,
            update=update_plan(
                [
                    PlanEntry(
                        content="Traiter la demande simulée",
                        priority="high",
                        status="completed",
                    )
                ]
            ),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        if session := self._sessions.get(session_id):
            session.cancel_event.set()

    async def set_session_mode(
        self, session_id: str, mode_id: str, **kwargs: Any
    ) -> SetSessionModeResponse | None:
        raise RequestError.method_not_found("session/set_mode")

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse | None:
        raise RequestError.method_not_found("session/set_config_option")

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
        raise RequestError.method_not_found("authenticate")

    async def fork_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio]
        | None = None,
        **kwargs: Any,
    ) -> ForkSessionResponse:
        raise RequestError.method_not_found("session/fork")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(f"_{method}")

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    async def _stream(self, session_id: str, text: str) -> None:
        chunks = [text[index : index + 12] for index in range(0, len(text), 12)]
        for chunk in chunks:
            await self._conn.session_update(
                session_id=session_id,
                update=update_agent_message(text_block(chunk)),
            )
            await asyncio.sleep(self._stream_delay)

    async def _ask_permission(self, session_id: str) -> bool:
        tool_id = f"fake-permission-{uuid4().hex}"
        await self._conn.session_update(
            session_id=session_id,
            update=start_tool_call(
                tool_id,
                "Supprimer build/ (simulation sans effet)",
                kind="execute",
                status="pending",
                raw_input={"command": ["rm", "-rf", "build/"]},
            ),
        )
        response: RequestPermissionResponse = await self._conn.request_permission(
            session_id=session_id,
            tool_call=ToolCallUpdate(
                tool_call_id=tool_id,
                kind="execute",
                title="Supprimer build/ (simulation sans effet)",
                raw_input={"command": ["rm", "-rf", "build/"]},
            ),
            options=[
                PermissionOption(option_id="deny", name="Refuser", kind="reject_once"),
                PermissionOption(option_id="once", name="Autoriser une fois", kind="allow_once"),
            ],
        )
        allowed = getattr(response.outcome, "option_id", None) == "once"
        await self._conn.session_update(
            session_id=session_id,
            update=update_tool_call(
                tool_id,
                status="completed" if allowed else "failed",
                raw_output={"allowed": allowed},
            ),
        )
        return allowed

    async def _mention_agent(
        self, session_id: str, session: _FakeSession, target_agent_id: str
    ) -> None:
        arguments = {
            "agent_id": target_agent_id,
            "content": "Délégation structurée du faux agent ACP.",
            "idempotency_key": f"fake:{session_id}:{session.prompt_count}:mention",
        }
        await self._call_fleet_tool(
            session_id,
            session,
            "fleet.mention_agent",
            arguments,
        )

    async def _delegate_task(
        self, session_id: str, session: _FakeSession, target_agent_id: str
    ) -> None:
        arguments = {
            "agent_id": target_agent_id,
            "title": "Tâche déléguée par le faux agent ACP",
            "description": (
                "Exécuter la prochaine étape déterministe de la démonstration Agent Fleet."
            ),
            "idempotency_key": f"fake:{session_id}:{session.prompt_count}:task",
            "priority": 2,
        }
        await self._call_fleet_tool(
            session_id,
            session,
            "fleet.delegate_task",
            arguments,
        )

    async def _complete_task(self, session_id: str, session: _FakeSession) -> None:
        # Aucun task_id libre : le Control Plane le dérive de la session ACP liée.
        arguments = {
            "result_summary": "Résultat déterministe du faux agent ACP.",
            "idempotency_key": f"fake:{session_id}:{session.prompt_count}:complete",
            "artifacts": [],
        }
        await self._call_fleet_tool(
            session_id,
            session,
            "fleet.complete_task",
            arguments,
        )

    async def _call_fleet_tool(
        self,
        session_id: str,
        session: _FakeSession,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        if not session.mcp_servers:
            raise RuntimeError("aucun serveur MCP local n'a été fourni")
        server = session.mcp_servers[0]
        tool_id = f"fake-delegate-{uuid4().hex}"
        await self._conn.session_update(
            session_id=session_id,
            update=start_tool_call(
                tool_id,
                tool_name,
                kind="other",
                status="in_progress",
                raw_input=arguments,
            ),
        )
        parameters = StdioServerParameters(
            command=server.command,
            args=server.args,
            env={item.name: item.value for item in server.env},
            cwd=session.cwd,
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                await client.initialize()
                result = await client.call_tool(tool_name, arguments)
        if result.isError:
            raise RuntimeError(f"l'appel {tool_name} simulé a échoué")
        await self._conn.session_update(
            session_id=session_id,
            update=update_tool_call(
                tool_id,
                status="completed",
                raw_output={"delegated": True},
            ),
        )

    @staticmethod
    def _matching_directive(
        text: str,
        unconditional: re.Pattern[str],
        conditional: re.Pattern[str],
    ) -> str | None:
        conditional_match = conditional.search(text)
        if conditional_match is not None:
            requester = conditional_match.group("requester").strip()
            if f"Demandeur : {requester}" in text:
                return conditional_match.group("agent")
            return None
        match = unconditional.search(text)
        return match.group("agent") if match is not None else None

    def _restore(
        self,
        session_id: str,
        cwd: str,
        mcp_servers: list[HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio] | None,
    ) -> None:
        self._sessions[session_id] = _FakeSession(
            cwd=os.path.realpath(cwd),
            mcp_servers=tuple(
                item for item in (mcp_servers or []) if isinstance(item, McpServerStdio)
            ),
        )
