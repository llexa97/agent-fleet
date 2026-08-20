"""Supervision des processus ACP et traduction du protocole worker."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from acp import PROTOCOL_VERSION as ACP_PROTOCOL_VERSION
from acp.schema import EnvVariable, McpServerStdio
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.contracts.enums import HarnessType
from packages.contracts.worker_protocol import (
    ControlMessageType,
    HarnessInventory,
    WireEnvelope,
    WorkerCapacity,
    WorkerInventory,
    WorkerMessageType,
    WorkspaceInventory,
    new_envelope,
)

from . import __version__
from .adapters import (
    ClaudeAcpAdapter,
    CodexAcpAdapter,
    FakeAcpAdapter,
    HarnessAdapter,
    HarnessProcess,
    OpenCodeAcpAdapter,
    SessionSpec,
)
from .config import HarnessConfig, WorkerConfig
from .errors import HarnessError, ProtocolError, UnsupportedCapabilityError
from .journal import StoredSession, WorkerJournal
from .mcp_relay import LocalMcpRelayServer, SessionIdentity
from .workspaces import WorkspaceResolver

Emitter = Callable[[WireEnvelope], Awaitable[None]]


class CommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_id: UUID
    generation: int = Field(ge=1)
    tenant_id: UUID
    space_id: UUID
    channel_id: UUID
    agent_id: UUID
    agent_actor_id: UUID
    harness: HarnessType
    model: str | None = Field(default=None, max_length=255)
    workspace_id: str = Field(min_length=1, max_length=100)
    system_prompt: str = Field(max_length=500_000)
    prompt: str = Field(max_length=500_000)
    context: dict[str, Any]

    @model_validator(mode="after")
    def context_identity_must_match(self) -> CommandPayload:
        agent = self.context.get("agent")
        channel = self.context.get("channel")
        if not isinstance(agent, dict) or str(agent.get("id")) != str(self.agent_id):
            raise ValueError("l'identité agent du contexte est incohérente")
        if not isinstance(channel, dict) or str(channel.get("id")) != str(self.channel_id):
            raise ValueError("le channel du contexte est incohérent")
        trace_id = self.context.get("trace_id")
        if trace_id is None:
            raise ValueError("trace_id manque dans le contexte")
        return self

    @property
    def task_id(self) -> UUID | None:
        value = self.context.get("task_id")
        return UUID(str(value)) if value else None

    @property
    def thread_id(self) -> UUID | None:
        value = self.context.get("thread_id")
        return UUID(str(value)) if value else None


class PermissionDecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    permission_request_id: UUID
    external_request_id: UUID
    decision: str = Field(min_length=1, max_length=64)


class ToolResultPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: UUID
    ok: bool
    result: Any = None
    error: dict[str, Any] | None = None


@dataclass(slots=True)
class PendingPermission:
    logical_session_id: UUID
    options: list[dict[str, Any]]
    future: asyncio.Future[str | None]


@dataclass(slots=True)
class ManagedSession:
    logical_session_id: UUID
    trace_id: UUID
    payload: CommandPayload
    adapter: HarnessAdapter
    handle: HarnessProcess
    relay_token_issued: bool
    was_resumed: bool = False
    sequence: int = 0
    final_chunks: list[str] = field(default_factory=list)
    published_via_tool: bool = False
    prompt_task: asyncio.Task[None] | None = None


class WorkerRuntime:
    def __init__(
        self,
        config: WorkerConfig,
        journal: WorkerJournal,
        resolver: WorkspaceResolver,
        relay: LocalMcpRelayServer,
        emit: Emitter,
    ) -> None:
        self.config = config
        self.journal = journal
        self.resolver = resolver
        self.relay = relay
        self._emit = emit
        self._sessions: dict[UUID, ManagedSession] = {}
        self._tool_results: dict[UUID, asyncio.Future[Any]] = {}
        self._permissions: dict[UUID, PendingPermission] = {}
        self._inventory_cache: tuple[float, list[HarnessInventory]] | None = None
        self._adapters = self._build_adapters()

    async def handle(self, envelope: WireEnvelope) -> list[WireEnvelope]:
        message_type = ControlMessageType(str(envelope.message_type))
        if message_type is ControlMessageType.SYNC_CONFIGURATION:
            if envelope.payload.get("acp_v2_experimental") is True:
                raise ProtocolError("ACP v2 expérimental est désactivé sur ce worker")
            return []
        if message_type is ControlMessageType.START_SESSION:
            await self._start(envelope, resume=False)
            return []
        if message_type is ControlMessageType.RESUME_SESSION:
            await self._start(envelope, resume=True)
            return []
        if message_type is ControlMessageType.PROMPT:
            await self._prompt(envelope)
            return []
        if message_type is ControlMessageType.CANCEL_PROMPT:
            await self._cancel(envelope)
            return []
        if message_type in {
            ControlMessageType.CLOSE_SESSION,
            ControlMessageType.SHUTDOWN_SESSION,
        }:
            await self._close(envelope)
            return []
        if message_type in {
            ControlMessageType.APPROVE_PERMISSION,
            ControlMessageType.DENY_PERMISSION,
        }:
            self._decide_permission(
                envelope,
                approved=message_type is ControlMessageType.APPROVE_PERMISSION,
            )
            return []
        if message_type is ControlMessageType.TOOL_RESULT:
            self._complete_tool(envelope)
            return []
        raise ProtocolError(f"commande worker non gérée: {message_type}")

    async def inventory(self) -> WorkerInventory:
        now = time.monotonic()
        if self._inventory_cache is None or now - self._inventory_cache[0] > 60:
            inventories: list[HarnessInventory] = []
            for harness_type, adapter in self._adapters.items():
                discovery = await adapter.discover()
                active_capabilities: set[str] = set()
                for session in self._sessions.values():
                    if session.adapter.harness_type is harness_type:
                        caps = session.handle.capabilities
                        active_capabilities.update(
                            name
                            for name, enabled in {
                                "load_session": caps.load_session,
                                "list_sessions": caps.list_sessions,
                                "resume_session": caps.resume_session,
                                "close_session": caps.close_session,
                                "additional_directories": caps.additional_directories,
                            }.items()
                            if enabled
                        )
                inventories.append(
                    HarnessInventory(
                        type=harness_type,
                        adapter=adapter.adapter_name,
                        version=discovery.version,
                        available=discovery.available,
                        capabilities=sorted(active_capabilities),
                    )
                )
            self._inventory_cache = (now, inventories)
        harnesses = self._inventory_cache[1]
        active = len(self._sessions)
        return WorkerInventory(
            worker_id=self.config.worker.id,
            hostname=self.config.worker.hostname,
            version=__version__,
            labels=list(self.config.worker.labels),
            capacity=WorkerCapacity(
                max_sessions=self.config.worker.max_sessions,
                available_sessions=max(0, self.config.worker.max_sessions - active),
            ),
            harnesses=harnesses,
            workspaces=[
                WorkspaceInventory(
                    id=item.id,
                    display_name=item.display_name,
                    root=str(item.root),
                    read_only=item.read_only,
                )
                for item in self.resolver.inventory()
            ],
        )

    async def shutdown(self) -> None:
        for session_id in list(self._sessions):
            session = self._sessions.pop(session_id)
            if session.prompt_task is not None:
                session.prompt_task.cancel()
            with contextlib.suppress(Exception):
                await session.adapter.terminate(session.handle)
            self.relay.revoke_session(session_id)
        for pending in self._permissions.values():
            if not pending.future.done():
                pending.future.set_result(None)
        self._permissions.clear()
        for future in self._tool_results.values():
            if not future.done():
                future.set_exception(HarnessError("arrêt du worker"))
        self._tool_results.clear()

    async def relay_tool_call(
        self,
        identity: SessionIdentity,
        tool_name: str,
        arguments: dict[str, Any],
        call_id: UUID,
    ) -> Any:
        session = self._sessions.get(identity.logical_session_id)
        if session is None:
            raise HarnessError("session MCP inconnue ou fermée")
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        if call_id in self._tool_results:
            raise HarnessError("call_id MCP déjà utilisé")
        self._tool_results[call_id] = future
        if tool_name in {
            "fleet.post_message",
            "fleet.reply_message",
            "fleet.mention_agent",
            "fleet.delegate_task",
            "fleet.complete_task",
        }:
            session.published_via_tool = True
        await self._emit(
            new_envelope(
                message_type=WorkerMessageType.TOOL_CALL,
                worker_id=self.config.worker.id,
                trace_id=session.trace_id,
                session_id=session.logical_session_id,
                idempotency_key=f"tool:{session.logical_session_id}:{call_id}",
                payload={
                    "call_id": str(call_id),
                    "tool_name": tool_name,
                    "arguments": arguments,
                },
            )
        )
        try:
            return await asyncio.wait_for(
                future,
                timeout=self.config.mcp_proxy.request_timeout_seconds,
            )
        finally:
            self._tool_results.pop(call_id, None)

    async def _start(self, envelope: WireEnvelope, *, resume: bool) -> None:
        logical_id, trace_id = self._ids(envelope)
        payload = CommandPayload.model_validate(envelope.payload)
        if trace_id != UUID(str(payload.context["trace_id"])):
            raise ProtocolError("trace_id du contexte incohérent")
        if logical_id in self._sessions:
            # Déduplication de second niveau en plus du journal de commandes.
            return
        session = await self._spawn_session(logical_id, trace_id, payload, resume=resume)
        self._sessions[logical_id] = session
        await self._emit_session_ready(session, resumed=session.was_resumed)
        self._schedule_prompt(session)

    async def _prompt(self, envelope: WireEnvelope) -> None:
        logical_id, trace_id = self._ids(envelope)
        payload = CommandPayload.model_validate(envelope.payload)
        session = self._sessions.get(logical_id)
        if session is None:
            session = await self._spawn_session(logical_id, trace_id, payload, resume=True)
            self._sessions[logical_id] = session
            await self._emit_session_ready(session, resumed=session.was_resumed)
        if session.prompt_task is not None and not session.prompt_task.done():
            raise HarnessError("un prompt est déjà actif pour cette session")
        session.payload = payload
        session.trace_id = trace_id
        session.final_chunks.clear()
        session.published_via_tool = False
        self._schedule_prompt(session)

    async def _spawn_session(
        self,
        logical_id: UUID,
        trace_id: UUID,
        payload: CommandPayload,
        *,
        resume: bool,
    ) -> ManagedSession:
        if len(self._sessions) >= self.config.worker.max_sessions:
            raise HarnessError("capacité maximale du worker atteinte")
        adapter = self._adapters.get(payload.harness)
        if adapter is None:
            raise HarnessError(f"harness {payload.harness.value!r} absent ou désactivé")
        workspace = self.resolver.get(payload.workspace_id)

        async def update_handler(
            acp_session_id: str, update_type: str, content: dict[str, Any]
        ) -> None:
            await self._on_session_update(logical_id, update_type, content)

        async def permission_handler(
            acp_session_id: str,
            tool_call: dict[str, Any],
            options: list[dict[str, Any]],
        ) -> str | None:
            return await self._request_permission(logical_id, tool_call, options)

        handle = await adapter.spawn(update_handler, permission_handler)
        try:
            await adapter.initialize(handle)
            mcp_servers, issued = self._mcp_server(logical_id, trace_id, payload)
            spec = SessionSpec(
                cwd=workspace.root,
                mcp_servers=mcp_servers,
                model=payload.model,
            )
            stored = await self.journal.load_session(logical_id) if resume else None
            resumed = False
            if stored is not None:
                try:
                    await adapter.resume_session(handle, stored.acp_session_id, spec)
                    resumed = True
                except UnsupportedCapabilityError:
                    resumed = False
                except Exception:
                    # Le processus ou le fournisseur peut avoir perdu sa session. Le
                    # contexte central du prompt permet alors une recréation explicite.
                    resumed = False
            if not resumed:
                await adapter.create_session(handle, spec)
            if handle.acp_session_id is None:
                raise HarnessError("le harness n'a retourné aucun identifiant de session ACP")
            managed = ManagedSession(
                logical_session_id=logical_id,
                trace_id=trace_id,
                payload=payload,
                adapter=adapter,
                handle=handle,
                relay_token_issued=issued,
                was_resumed=resumed,
                sequence=(int(stored.metadata.get("sequence", 0)) if stored is not None else 0),
            )
            await self.journal.save_session(self._stored(managed, "active"))
            self._inventory_cache = None
            return managed
        except BaseException:
            self.relay.revoke_session(logical_id)
            await adapter.terminate(handle)
            raise

    def _mcp_server(
        self, logical_id: UUID, trace_id: UUID, payload: CommandPayload
    ) -> tuple[tuple[McpServerStdio, ...], bool]:
        if not self.config.mcp_proxy.enabled:
            return (), False
        identity = SessionIdentity(
            logical_session_id=logical_id,
            acp_session_id=f"pending:{logical_id}",
            tenant_id=payload.tenant_id,
            space_id=payload.space_id,
            agent_id=payload.agent_id,
            channel_id=payload.channel_id,
            task_id=payload.task_id,
            trace_id=trace_id,
        )
        token = self.relay.issue_token(identity, self.config.mcp_proxy.token_ttl_seconds)
        repository_root = Path(__file__).resolve().parents[2]
        return (
            (
                McpServerStdio(
                    name="Agent Fleet",
                    command=str(self.config.mcp_proxy.executable),
                    args=list(self.config.mcp_proxy.args),
                    env=[
                        EnvVariable(
                            name="AGENT_FLEET_MCP_SOCKET",
                            value=str(self.config.relay_socket_path),
                        ),
                        EnvVariable(
                            name="AGENT_FLEET_MCP_TOKEN",
                            value=token.get_secret_value(),
                        ),
                        EnvVariable(
                            name="AGENT_FLEET_MCP_TIMEOUT_SECONDS",
                            value=str(self.config.mcp_proxy.request_timeout_seconds),
                        ),
                        EnvVariable(name="PYTHONPATH", value=str(repository_root)),
                    ],
                ),
            ),
            True,
        )

    def _schedule_prompt(self, session: ManagedSession) -> None:
        session.prompt_task = asyncio.create_task(
            self._run_prompt(session),
            name=f"acp-prompt-{session.logical_session_id}",
        )

    async def _run_prompt(self, session: ManagedSession) -> None:
        payload = session.payload
        controlled_prompt = f"{payload.system_prompt}\n\n--- DEMANDE ---\n{payload.prompt}"
        try:
            result = await session.adapter.prompt(session.handle, controlled_prompt)
            usage = result.get("usage") if isinstance(result, dict) else None
            tokens, cost = self._usage(usage)
            await self.journal.save_session(self._stored(session, "completed"))
            completed = new_envelope(
                message_type=WorkerMessageType.SESSION_COMPLETED,
                worker_id=self.config.worker.id,
                trace_id=session.trace_id,
                session_id=session.logical_session_id,
                idempotency_key=(f"completed:{payload.delivery_id}:{payload.generation}"),
                payload={
                    "delivery_id": str(payload.delivery_id),
                    "generation": payload.generation,
                    "final_text": "".join(session.final_chunks),
                    "published_via_tool": session.published_via_tool,
                    "stop_reason": result.get("stop_reason", "end_turn"),
                    "tokens": tokens,
                    "cost_eur": cost,
                },
            )
            # L'isolation MVP est per-session-active. Une fois le tour terminé,
            # conserver uniquement l'identifiant ACP durable libère la capacité
            # locale. Le prochain prompt recrée un processus et tente session/load,
            # la capacité stable négociée par ACP v1.
            await session.adapter.terminate(session.handle)
            self.relay.revoke_session(session.logical_session_id)
            self._sessions.pop(session.logical_session_id, None)
            self._inventory_cache = None
            await self._emit(completed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.journal.save_session(self._stored(session, "failed"))
            await self._emit(
                new_envelope(
                    message_type=WorkerMessageType.SESSION_FAILED,
                    worker_id=self.config.worker.id,
                    trace_id=session.trace_id,
                    session_id=session.logical_session_id,
                    idempotency_key=f"failed:{payload.delivery_id}:{payload.generation}",
                    payload={
                        "delivery_id": str(payload.delivery_id),
                        "generation": payload.generation,
                        "error": {
                            "code": "harness_prompt_failed",
                            "type": type(exc).__name__,
                            "retryable": True,
                        },
                    },
                )
            )
            await session.adapter.terminate(session.handle)
            self.relay.revoke_session(session.logical_session_id)
            self._sessions.pop(session.logical_session_id, None)
            self._inventory_cache = None

    async def _on_session_update(
        self, logical_id: UUID, update_type: str, content: dict[str, Any]
    ) -> None:
        session = self._sessions.get(logical_id)
        # create_session peut produire une mise à jour avant l'enregistrement final ;
        # elle n'est pas associable à une livraison et est donc ignorée explicitement.
        if session is None:
            return
        if update_type == "agent_message_chunk":
            block = content.get("content")
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    session.final_chunks.append(text)
        session.sequence += 1
        payload = session.payload
        await self._emit(
            new_envelope(
                message_type=WorkerMessageType.SESSION_UPDATE,
                worker_id=self.config.worker.id,
                trace_id=session.trace_id,
                session_id=logical_id,
                idempotency_key=(
                    f"update:{payload.delivery_id}:{payload.generation}:{session.sequence}"
                ),
                payload={
                    "delivery_id": str(payload.delivery_id),
                    "generation": payload.generation,
                    "sequence": session.sequence,
                    "update_type": update_type,
                    "content": content,
                },
            )
        )
        if update_type == "usage":
            tokens, cost = self._usage(content)
            await self._emit(
                new_envelope(
                    message_type=WorkerMessageType.USAGE_UPDATE,
                    worker_id=self.config.worker.id,
                    trace_id=session.trace_id,
                    session_id=logical_id,
                    idempotency_key=(
                        f"usage:{payload.delivery_id}:{payload.generation}:{session.sequence}"
                    ),
                    payload={"tokens": tokens, "cost_eur": cost},
                )
            )

    async def _request_permission(
        self,
        logical_id: UUID,
        tool_call: dict[str, Any],
        options: list[dict[str, Any]],
    ) -> str | None:
        session = self._sessions.get(logical_id)
        if session is None:
            return None
        request_id = uuid4()
        future: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()
        self._permissions[request_id] = PendingPermission(logical_id, options, future)
        payload = session.payload
        await self._emit(
            new_envelope(
                message_type=WorkerMessageType.PERMISSION_REQUEST,
                worker_id=self.config.worker.id,
                trace_id=session.trace_id,
                session_id=logical_id,
                idempotency_key=f"permission:{logical_id}:{request_id}",
                payload={
                    "delivery_id": str(payload.delivery_id),
                    "generation": payload.generation,
                    "request_id": str(request_id),
                    "capability": str(tool_call.get("kind", "unknown")),
                    "summary": str(tool_call.get("title", "Action du harness"))[:500],
                    "details": {"tool_call": tool_call, "options": options},
                },
            )
        )
        try:
            # Pas de timeout autorisant implicitement : l'absence de décision maintient
            # le prompt en attente jusqu'à annulation ou décision humaine.
            return await future
        finally:
            self._permissions.pop(request_id, None)

    def _decide_permission(self, envelope: WireEnvelope, *, approved: bool) -> None:
        decision = PermissionDecisionPayload.model_validate(envelope.payload)
        pending = self._permissions.get(decision.external_request_id)
        if pending is None or pending.future.done():
            return
        if not approved:
            pending.future.set_result(None)
            return
        preferred_kind = {
            "allow_once": "allow_once",
            "allow_session": "allow_always",
            "allow_agent": "allow_always",
        }.get(decision.decision, "allow_once")
        choice = next(
            (
                str(item["option_id"])
                for item in pending.options
                if item.get("kind") == preferred_kind
            ),
            None,
        )
        if choice is None:
            choice = next(
                (
                    str(item["option_id"])
                    for item in pending.options
                    if str(item.get("kind", "")).startswith("allow")
                ),
                None,
            )
        pending.future.set_result(choice)

    def _complete_tool(self, envelope: WireEnvelope) -> None:
        result = ToolResultPayload.model_validate(envelope.payload)
        future = self._tool_results.get(result.call_id)
        if future is None or future.done():
            return
        if result.ok:
            future.set_result(result.result)
        else:
            code = result.error.get("code", "tool_failed") if result.error else "tool_failed"
            future.set_exception(HarnessError(f"appel fleet.* refusé: {code}"))

    async def _cancel(self, envelope: WireEnvelope) -> None:
        logical_id, _ = self._ids(envelope)
        session = self._sessions.get(logical_id)
        if session is None:
            return
        await session.adapter.cancel(session.handle)
        for pending in self._permissions.values():
            if pending.logical_session_id == logical_id and not pending.future.done():
                pending.future.set_result(None)
        await self._on_session_update(logical_id, "status", {"status": "cancelled"})

    async def _close(self, envelope: WireEnvelope) -> None:
        logical_id, _ = self._ids(envelope)
        session = self._sessions.pop(logical_id, None)
        if session is None:
            return
        if session.prompt_task is not None and not session.prompt_task.done():
            await session.adapter.cancel(session.handle)
            session.prompt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.prompt_task
        with contextlib.suppress(Exception):
            await session.adapter.close_session(session.handle)
        await session.adapter.terminate(session.handle)
        self.relay.revoke_session(logical_id)
        await self.journal.save_session(self._stored(session, "closed"))
        self._inventory_cache = None

    async def _emit_session_ready(self, session: ManagedSession, *, resumed: bool) -> None:
        payload = session.payload
        caps = session.handle.capabilities
        await self._emit(
            new_envelope(
                message_type=(
                    WorkerMessageType.SESSION_RESUMED
                    if resumed
                    else WorkerMessageType.SESSION_STARTED
                ),
                worker_id=self.config.worker.id,
                trace_id=session.trace_id,
                session_id=session.logical_session_id,
                idempotency_key=(
                    f"session-ready:{payload.delivery_id}:{payload.generation}:"
                    f"{'resume' if resumed else 'new'}"
                ),
                payload={
                    "delivery_id": str(payload.delivery_id),
                    "generation": payload.generation,
                    "harness_session_id": session.handle.acp_session_id,
                    "acp_protocol_version": str(ACP_PROTOCOL_VERSION),
                    "capabilities": {
                        "load_session": caps.load_session,
                        "list_sessions": caps.list_sessions,
                        "resume_session": caps.resume_session,
                        "close_session": caps.close_session,
                        "additional_directories": caps.additional_directories,
                        "config_options": sorted(session.handle.config_options),
                    },
                },
            )
        )

    def _stored(self, session: ManagedSession, state: str) -> StoredSession:
        return StoredSession(
            logical_session_id=session.logical_session_id,
            acp_session_id=session.handle.acp_session_id or "unknown",
            harness_type=session.adapter.harness_type.value,
            workspace_id=session.payload.workspace_id,
            state=state,
            metadata={
                "tenant_id": str(session.payload.tenant_id),
                "space_id": str(session.payload.space_id),
                "agent_id": str(session.payload.agent_id),
                "channel_id": str(session.payload.channel_id),
                "task_id": str(session.payload.task_id) if session.payload.task_id else None,
                "trace_id": str(session.trace_id),
                "sequence": session.sequence,
            },
        )

    def _build_adapters(self) -> dict[HarnessType, HarnessAdapter]:
        classes: dict[HarnessType, Callable[[HarnessConfig], HarnessAdapter]] = {
            HarnessType.CODEX: CodexAcpAdapter,
            HarnessType.CLAUDE: ClaudeAcpAdapter,
            HarnessType.FAKE: FakeAcpAdapter,
            HarnessType.OPENCODE: OpenCodeAcpAdapter,
        }
        adapters: dict[HarnessType, HarnessAdapter] = {}
        for harness_type, harness_config in self.config.harnesses.items():
            adapter_class = classes.get(harness_type)
            if harness_config.enabled and adapter_class is not None:
                adapters[harness_type] = adapter_class(harness_config)
        if not adapters:
            raise HarnessError("aucun adaptateur ACP supporté n'est activé")
        return adapters

    @staticmethod
    def _ids(envelope: WireEnvelope) -> tuple[UUID, UUID]:
        if envelope.session_id is None or envelope.trace_id is None:
            raise ProtocolError("session_id et trace_id sont requis")
        return envelope.session_id, envelope.trace_id

    @staticmethod
    def _usage(usage: Any) -> tuple[int, str]:
        if not isinstance(usage, dict):
            return 0, "0"
        tokens = int(usage.get("used", usage.get("total_tokens", 0)) or 0)
        cost = usage.get("cost")
        if isinstance(cost, dict) and str(cost.get("currency", "")).upper() == "EUR":
            return max(0, tokens), str(cost.get("amount", "0"))
        return max(0, tokens), "0"
