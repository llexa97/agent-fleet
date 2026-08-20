"""Adaptateur générique ACP v1 sur stdin/stdout, basé sur le SDK officiel."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
from pathlib import Path
from typing import Any, cast

from acp import PROTOCOL_VERSION, connect_to_agent, text_block
from acp.schema import (
    ClientCapabilities,
    FileSystemCapabilities,
    Implementation,
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
)

from services.worker import __version__
from services.worker.config import HarnessConfig
from services.worker.errors import (
    HarnessError,
    HarnessUnavailableError,
    UnsupportedCapabilityError,
)

from .base import (
    AdapterCapabilities,
    HarnessAdapter,
    HarnessDiscovery,
    HarnessProcess,
    PermissionHandler,
    SessionSpec,
    UpdateHandler,
)
from .client import FleetAcpClient

_SAFE_BASE_ENV = {
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
    "USER",
}
_SECRET_PATTERN = re.compile(
    r"(?i)(?:sk-(?:ant-|proj-)?[a-z0-9_-]{12,}|bearer\s+[a-z0-9._~-]{12,})"
)


class AcpStdioAdapter(HarnessAdapter):
    def __init__(self, config: HarnessConfig) -> None:
        self.config = config
        self._active_instances = 0
        self._capacity_lock = asyncio.Lock()

    async def discover(self) -> HarnessDiscovery:
        executable = self.config.executable
        if not executable.is_file() or not os.access(executable, os.X_OK):
            return HarnessDiscovery(available=False, error="exécutable absent ou non exécutable")
        if not self.config.version_args:
            return HarnessDiscovery(available=True)
        try:
            process = await asyncio.create_subprocess_exec(
                str(executable),
                *self.config.version_args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._environment(),
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self.config.startup_timeout_seconds
            )
        except (OSError, TimeoutError) as exc:
            return HarnessDiscovery(available=False, error=type(exc).__name__)
        output = stdout.decode("utf-8", errors="replace").strip().splitlines()
        return HarnessDiscovery(
            available=process.returncode == 0,
            version=self._redact(output[0][:120]) if output else None,
            error=None if process.returncode == 0 else f"code de sortie {process.returncode}",
        )

    async def health_check(self) -> bool:
        return (await self.discover()).available

    async def spawn(
        self, update_handler: UpdateHandler, permission_handler: PermissionHandler
    ) -> HarnessProcess:
        async with self._capacity_lock:
            if self._active_instances >= self.config.max_instances:
                raise HarnessUnavailableError("capacité maximale du harness atteinte")
            self._active_instances += 1
        try:
            process = await asyncio.create_subprocess_exec(
                str(self.config.executable),
                *self.config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._environment(),
                start_new_session=True,
                limit=50 * 1024 * 1024,
            )
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise HarnessError("les tubes stdin/stdout/stderr du harness sont indisponibles")
            client = FleetAcpClient(update_handler, permission_handler)
            connection = connect_to_agent(client, process.stdin, process.stdout)
            handle = HarnessProcess(process=process, connection=connection, client=client)
            handle.stderr_task = asyncio.create_task(
                self._drain_stderr(process.stderr, handle.stderr_tail),
                name=f"{self.adapter_name}-stderr-{process.pid}",
            )
            return handle
        except BaseException:
            await self._release_capacity()
            raise

    async def initialize(self, handle: HarnessProcess) -> AdapterCapabilities:
        try:
            response = await asyncio.wait_for(
                handle.connection.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(
                        fs=FileSystemCapabilities(
                            read_text_file=False,
                            write_text_file=False,
                        ),
                        terminal=False,
                    ),
                    client_info=Implementation(
                        name="agent-fleet-worker",
                        title="Agent Fleet Worker",
                        version=__version__,
                    ),
                ),
                timeout=self.config.startup_timeout_seconds,
            )
        except Exception as exc:
            raise HarnessError(f"échec de l'initialisation ACP: {type(exc).__name__}") from exc
        if response.protocol_version != PROTOCOL_VERSION:
            raise HarnessError(
                "version ACP incompatible: "
                f"{response.protocol_version} (attendue {PROTOCOL_VERSION})"
            )
        advertised = response.agent_capabilities
        session = advertised.session_capabilities if advertised is not None else None
        capabilities = AdapterCapabilities(
            load_session=bool(advertised and advertised.load_session),
            list_sessions=bool(session and session.list is not None),
            resume_session=bool(session and session.resume is not None),
            close_session=bool(session and session.close is not None),
            additional_directories=bool(session and session.additional_directories is not None),
        )
        handle.capabilities = capabilities
        return capabilities

    async def create_session(self, handle: HarnessProcess, spec: SessionSpec) -> str:
        additional = self._additional_directories(handle, spec)
        response = await handle.connection.new_session(
            cwd=str(spec.cwd),
            additional_directories=additional,
            mcp_servers=list(spec.mcp_servers),
        )
        handle.acp_session_id = response.session_id
        handle.config_options = frozenset(option.id for option in (response.config_options or []))
        if spec.model is not None:
            await self._configure_model(handle, response.config_options or [], spec.model)
        return str(response.session_id)

    async def resume_session(
        self, handle: HarnessProcess, acp_session_id: str, spec: SessionSpec
    ) -> None:
        additional = self._additional_directories(handle, spec)
        if handle.capabilities.resume_session:
            response = await handle.connection.resume_session(
                session_id=acp_session_id,
                cwd=str(spec.cwd),
                additional_directories=additional,
                mcp_servers=list(spec.mcp_servers),
            )
        elif handle.capabilities.load_session:
            response = await handle.connection.load_session(
                session_id=acp_session_id,
                cwd=str(spec.cwd),
                additional_directories=additional,
                mcp_servers=list(spec.mcp_servers),
            )
        else:
            raise UnsupportedCapabilityError(
                "le harness n'annonce ni resume_session ni load_session"
            )
        handle.acp_session_id = acp_session_id
        handle.config_options = frozenset(
            option.id for option in (getattr(response, "config_options", None) or [])
        )
        if spec.model is not None:
            await self._configure_model(
                handle, getattr(response, "config_options", None) or [], spec.model
            )

    async def list_sessions(self, handle: HarnessProcess, cwd: Path | None = None) -> list[str]:
        if not handle.capabilities.list_sessions:
            raise UnsupportedCapabilityError("le harness n'annonce pas session/list")
        cursor: str | None = None
        sessions: list[str] = []
        while True:
            response = await handle.connection.list_sessions(
                cwd=str(cwd) if cwd is not None else None,
                cursor=cursor,
            )
            sessions.extend(item.session_id for item in response.sessions)
            cursor = response.next_cursor
            if cursor is None:
                return sessions

    async def prompt(self, handle: HarnessProcess, text: str) -> dict[str, Any]:
        if handle.acp_session_id is None:
            raise HarnessError("aucune session ACP n'est attachée au processus")
        try:
            response = await asyncio.wait_for(
                handle.connection.prompt(
                    session_id=handle.acp_session_id,
                    prompt=[text_block(text)],
                ),
                timeout=self.config.prompt_timeout_seconds,
            )
        except TimeoutError:
            with contextlib.suppress(Exception):
                await handle.connection.cancel(session_id=handle.acp_session_id)
            raise
        return cast(
            dict[str, Any],
            response.model_dump(mode="json", by_alias=False, exclude_none=True),
        )

    async def cancel(self, handle: HarnessProcess) -> None:
        if handle.acp_session_id is not None:
            await handle.connection.cancel(session_id=handle.acp_session_id)

    async def close_session(self, handle: HarnessProcess) -> None:
        if handle.acp_session_id is None:
            return
        if handle.capabilities.close_session:
            await handle.connection.close_session(session_id=handle.acp_session_id)
        else:
            await self.cancel(handle)

    async def terminate(self, handle: HarnessProcess) -> None:
        if handle.capacity_released:
            return
        with contextlib.suppress(Exception):
            await asyncio.wait_for(handle.connection.close(), timeout=2)
        process = handle.process
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=5)
        if handle.stderr_task is not None:
            handle.stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await handle.stderr_task
        handle.capacity_released = True
        await self._release_capacity()

    def parse_updates(self, update: Any) -> tuple[str, dict[str, Any]] | None:
        return FleetAcpClient.classify_update(update)

    def _environment(self) -> dict[str, str]:
        allowed = _SAFE_BASE_ENV | set(self.config.env_allowlist)
        return {name: value for name, value in os.environ.items() if name in allowed}

    def _additional_directories(
        self, handle: HarnessProcess, spec: SessionSpec
    ) -> list[str] | None:
        if not spec.additional_directories:
            return None
        if not handle.capabilities.additional_directories:
            raise UnsupportedCapabilityError(
                "le harness n'annonce pas la capacité additionalDirectories"
            )
        return [str(item) for item in spec.additional_directories]

    async def _configure_model(
        self,
        handle: HarnessProcess,
        options: list[Any],
        requested_model: str,
    ) -> None:
        model_option = next(
            (
                option
                for option in options
                if isinstance(option, SessionConfigOptionSelect)
                and (option.category == "model" or option.id == "model")
            ),
            None,
        )
        if model_option is None:
            raise UnsupportedCapabilityError("le harness n'annonce aucune option de modèle")
        allowed_values: set[str] = set()
        for option in model_option.options:
            if isinstance(option, SessionConfigSelectGroup):
                allowed_values.update(item.value for item in option.options)
            else:
                allowed_values.add(option.value)
        if requested_model not in allowed_values:
            raise UnsupportedCapabilityError("le modèle demandé n'est pas annoncé par le harness")
        await handle.connection.set_config_option(
            session_id=handle.acp_session_id,
            config_id=model_option.id,
            value=requested_model,
        )

    async def _drain_stderr(
        self, stream: asyncio.StreamReader, tail: list[str], max_lines: int = 250
    ) -> None:
        while line := await stream.readline():
            tail.append(self._redact(line.decode("utf-8", errors="replace").rstrip())[:1000])
            del tail[:-max_lines]

    @staticmethod
    def _redact(value: str) -> str:
        return _SECRET_PATTERN.sub("[SECRET_REDACTED]", value)

    async def _release_capacity(self) -> None:
        async with self._capacity_lock:
            self._active_instances = max(0, self._active_instances - 1)
