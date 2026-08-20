from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from packages.contracts.enums import HarnessType
from services.worker.config import (
    ControlPlaneConfig,
    HarnessConfig,
    WorkerConfig,
    WorkerIdentityConfig,
    WorkspaceConfig,
)
from services.worker.errors import ConfigurationError, WorkspaceAccessError
from services.worker.workspaces import WorkspaceResolver


def test_worker_control_plane_requires_wss_or_explicit_exact_development_host() -> None:
    with pytest.raises(ValidationError):
        ControlPlaneConfig(url="ws://api:8000/api/v1/workers/connect")

    config = ControlPlaneConfig(
        url="ws://api:8000/api/v1/workers/connect",
        allowed_insecure_hosts=("api",),
    )
    assert config.url.startswith("ws://api:")

    with pytest.raises(ValidationError):
        ControlPlaneConfig(
            url="ws://api:8000/api/v1/workers/connect",
            allowed_insecure_hosts=("*.internal",),
        )


def test_worker_token_is_only_resolved_from_named_environment() -> None:
    config = ControlPlaneConfig(url="wss://control.example.test/worker", token_env="FLEET_TOKEN")
    with pytest.raises(ConfigurationError):
        config.resolve_token({"FLEET_TOKEN": "court"})
    token = "x" * 48
    assert config.resolve_token({"FLEET_TOKEN": token}).get_secret_value() == token


def test_worker_config_rejects_relative_executables_and_duplicate_workspaces(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError):
        HarnessConfig(executable=Path("codex-acp"))

    workspace = WorkspaceConfig(id="project", display_name="Projet", root=tmp_path)
    with pytest.raises(ValidationError):
        WorkerConfig(
            worker=WorkerIdentityConfig(id=uuid4(), state_dir=tmp_path / "state"),
            control_plane=ControlPlaneConfig(url="wss://control.example.test/worker"),
            harnesses={HarnessType.FAKE: HarnessConfig(executable=Path("/bin/true"))},
            workspaces=(workspace, workspace),
        )


def test_worker_workspace_resolution_blocks_traversal_symlinks_and_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    resolver = WorkspaceResolver(
        [WorkspaceConfig(id="safe", display_name="Safe", root=root, read_only=True)]
    )

    assert resolver.resolve("safe", "file.txt") == root / "file.txt"
    with pytest.raises(WorkspaceAccessError):
        resolver.resolve("safe", "../outside/secret.txt", must_exist=True)
    with pytest.raises(WorkspaceAccessError):
        resolver.resolve("safe", "escape/secret.txt", must_exist=True)
    with pytest.raises(WorkspaceAccessError):
        resolver.resolve("safe", "new.txt", require_write=True)
    with pytest.raises(WorkspaceAccessError):
        resolver.resolve("missing")
