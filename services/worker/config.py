"""Chargement strict de la configuration YAML d'un worker.

Les secrets sont référencés par nom de variable d'environnement. Ils ne sont ni
sérialisés dans l'inventaire ni inclus dans les représentations Pydantic.
"""

from __future__ import annotations

import os
import re
import socket
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from packages.contracts.enums import HarnessType

from .errors import ConfigurationError

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ControlPlaneConfig(StrictModel):
    url: str = Field(min_length=8, max_length=2048)
    token_env: str = "AGENT_FLEET_WORKER_TOKEN"
    connect_timeout_seconds: float = Field(default=15.0, ge=1, le=120)
    heartbeat_seconds: float = Field(default=15.0, ge=2, le=300)
    stale_after_seconds: float = Field(default=45.0, ge=5, le=900)
    max_message_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    backoff_initial_seconds: float = Field(default=1.0, ge=0.05, le=60)
    backoff_max_seconds: float = Field(default=30.0, ge=0.1, le=600)
    backoff_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    allow_insecure_localhost: bool = False
    allowed_insecure_hosts: tuple[str, ...] = ()
    ca_file: Path | None = None

    @field_validator("token_env")
    @classmethod
    def valid_token_environment_name(cls, value: str) -> str:
        if not _ENV_NAME.fullmatch(value):
            raise ValueError("token_env doit être un nom de variable d'environnement")
        return value

    @field_validator("url")
    @classmethod
    def secure_websocket_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError("url doit être une URL ws:// ou wss:// absolue")
        if parsed.username or parsed.password or parsed.fragment:
            raise ValueError("url ne doit contenir ni identifiants ni fragment")
        return value

    @field_validator("allowed_insecure_hosts")
    @classmethod
    def valid_explicit_insecure_hosts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        hosts = tuple(dict.fromkeys(host.strip().lower() for host in value if host.strip()))
        if any(len(host) > 253 or "/" in host or "*" in host or ":" in host for host in hosts):
            raise ValueError("allowed_insecure_hosts doit contenir des noms d'hôtes exacts")
        return hosts

    @model_validator(mode="after")
    def insecure_only_for_explicit_local_development(self) -> ControlPlaneConfig:
        from urllib.parse import urlsplit

        parsed = urlsplit(self.url)
        hostname = parsed.hostname
        if hostname is None:
            raise ValueError("url doit contenir un nom d'hôte")
        insecure_hosts = _LOCAL_HOSTS if self.allow_insecure_localhost else set()
        insecure_hosts = insecure_hosts | set(self.allowed_insecure_hosts)
        if parsed.scheme == "ws" and hostname.lower() not in insecure_hosts:
            raise ValueError(
                "wss:// est obligatoire, sauf hôte de développement explicitement autorisé"
            )
        if self.backoff_max_seconds < self.backoff_initial_seconds:
            raise ValueError("backoff_max_seconds doit être >= backoff_initial_seconds")
        return self

    def resolve_token(self, environ: Mapping[str, str] | None = None) -> SecretStr:
        source = os.environ if environ is None else environ
        value = source.get(self.token_env, "")
        if len(value) < 32:
            raise ConfigurationError(
                f"La variable {self.token_env} doit contenir un jeton d'au moins 32 caractères"
            )
        return SecretStr(value)


class WorkerIdentityConfig(StrictModel):
    id: UUID
    hostname: str = Field(default_factory=socket.gethostname, min_length=1, max_length=255)
    labels: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    max_sessions: int = Field(default=4, ge=1, le=128)
    state_dir: Path = Path("/var/lib/agent-fleet-worker")

    @field_validator("state_dir")
    @classmethod
    def absolute_state_directory(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("worker.state_dir doit être absolu")
        return value

    @field_validator("labels")
    @classmethod
    def clean_labels(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        labels = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 64 for item in labels):
            raise ValueError("un label ne peut pas dépasser 64 caractères")
        return labels


class HarnessConfig(StrictModel):
    executable: Path
    args: tuple[str, ...] = ()
    enabled: bool = True
    max_instances: int = Field(default=1, ge=1, le=128)
    env_allowlist: tuple[str, ...] = ()
    version_args: tuple[str, ...] = ("--version",)
    startup_timeout_seconds: float = Field(default=20, ge=1, le=300)
    prompt_timeout_seconds: float = Field(default=1800, ge=1, le=86_400)

    @field_validator("executable")
    @classmethod
    def absolute_executable(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("le chemin d'exécutable doit être absolu")
        return value

    @field_validator("args", "version_args")
    @classmethod
    def safe_fixed_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 64 or any("\x00" in item or len(item) > 4096 for item in value):
            raise ValueError("arguments d'exécutable invalides")
        return value

    @field_validator("env_allowlist")
    @classmethod
    def valid_environment_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unique = tuple(dict.fromkeys(value))
        if any(not _ENV_NAME.fullmatch(item) for item in unique):
            raise ValueError("env_allowlist contient un nom invalide")
        return unique


class WorkspaceConfig(StrictModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    display_name: str = Field(min_length=1, max_length=160)
    root: Path
    read_only: bool = False

    @field_validator("root")
    @classmethod
    def absolute_workspace_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("la racine d'un workspace doit être absolue")
        return value


class McpProxyConfig(StrictModel):
    enabled: bool = True
    # Ne pas résoudre le lien d'un Python de virtualenv : le binaire cible brut
    # perdrait alors son environnement site-packages.
    executable: Path = Field(default_factory=lambda: Path(sys.executable))
    args: tuple[str, ...] = ("-m", "services.fleet_mcp_proxy")
    request_timeout_seconds: float = Field(default=60, ge=1, le=3600)
    token_ttl_seconds: int = Field(default=86_400, ge=60, le=604_800)

    @field_validator("executable")
    @classmethod
    def absolute_proxy_executable(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("mcp_proxy.executable doit être absolu")
        return value


class WorkerConfig(StrictModel):
    worker: WorkerIdentityConfig
    control_plane: ControlPlaneConfig
    harnesses: dict[HarnessType, HarnessConfig]
    workspaces: tuple[WorkspaceConfig, ...]
    mcp_proxy: McpProxyConfig = Field(default_factory=McpProxyConfig)

    @model_validator(mode="after")
    def unique_workspaces_and_usable_harness(self) -> WorkerConfig:
        ids = [workspace.id for workspace in self.workspaces]
        if len(ids) != len(set(ids)):
            raise ValueError("les identifiants de workspace doivent être uniques")
        if not any(item.enabled for item in self.harnesses.values()):
            raise ValueError("au moins un harness doit être activé")
        return self

    @property
    def journal_path(self) -> Path:
        return self.worker.state_dir / "worker-journal.sqlite3"

    @property
    def relay_socket_path(self) -> Path:
        return self.worker.state_dir / "fleet-mcp.sock"


def load_worker_config(path: Path) -> WorkerConfig:
    """Charge un YAML avec ``safe_load`` et valide tous les champs.

    La valeur du token n'est volontairement pas lue ici : ``resolve_token`` la
    récupère au dernier moment depuis l'environnement du service systemd.
    """

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Impossible de charger {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("La configuration YAML doit être un objet")
    try:
        return WorkerConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigurationError(f"Configuration worker invalide: {exc}") from exc


def redacted_config(config: WorkerConfig) -> dict[str, Any]:
    """Retourne une représentation journalisable qui ne résout aucun secret."""

    return config.model_dump(mode="json")
