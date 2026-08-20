"""Contrat interne, indépendant d'un fournisseur, des adaptateurs ACP."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp.schema import McpServerStdio

from packages.contracts.enums import HarnessType

UpdateHandler = Callable[[str, str, dict[str, Any]], Awaitable[None]]
PermissionHandler = Callable[[str, dict[str, Any], list[dict[str, Any]]], Awaitable[str | None]]


@dataclass(frozen=True, slots=True)
class HarnessDiscovery:
    available: bool
    version: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    load_session: bool = False
    list_sessions: bool = False
    resume_session: bool = False
    close_session: bool = False
    additional_directories: bool = False
    config_options: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SessionSpec:
    cwd: Path
    mcp_servers: tuple[McpServerStdio, ...] = ()
    additional_directories: tuple[Path, ...] = ()
    model: str | None = None


@dataclass(slots=True)
class HarnessProcess:
    process: Any
    connection: Any
    client: Any
    capabilities: AdapterCapabilities = field(default_factory=AdapterCapabilities)
    acp_session_id: str | None = None
    config_options: frozenset[str] = frozenset()
    stderr_task: Any = None
    stderr_tail: list[str] = field(default_factory=list)
    capacity_released: bool = False


class HarnessAdapter(ABC):
    """Un processus ACP isolé est créé pour chaque session active."""

    harness_type: HarnessType
    adapter_name: str

    @abstractmethod
    async def discover(self) -> HarnessDiscovery: ...

    @abstractmethod
    async def health_check(self) -> bool: ...

    @abstractmethod
    async def spawn(
        self, update_handler: UpdateHandler, permission_handler: PermissionHandler
    ) -> HarnessProcess: ...

    @abstractmethod
    async def initialize(self, handle: HarnessProcess) -> AdapterCapabilities: ...

    @abstractmethod
    async def create_session(self, handle: HarnessProcess, spec: SessionSpec) -> str: ...

    @abstractmethod
    async def resume_session(
        self, handle: HarnessProcess, acp_session_id: str, spec: SessionSpec
    ) -> None: ...

    @abstractmethod
    async def list_sessions(self, handle: HarnessProcess, cwd: Path | None = None) -> list[str]: ...

    @abstractmethod
    async def prompt(self, handle: HarnessProcess, text: str) -> dict[str, Any]: ...

    @abstractmethod
    async def cancel(self, handle: HarnessProcess) -> None: ...

    @abstractmethod
    async def close_session(self, handle: HarnessProcess) -> None: ...

    @abstractmethod
    async def terminate(self, handle: HarnessProcess) -> None: ...

    @abstractmethod
    def parse_updates(self, update: Any) -> tuple[str, dict[str, Any]] | None: ...
