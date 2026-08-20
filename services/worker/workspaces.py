"""Résolution canonique et confinée des workspaces enregistrés."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import WorkspaceConfig
from .errors import WorkspaceAccessError


@dataclass(frozen=True, slots=True)
class RegisteredWorkspace:
    id: str
    display_name: str
    root: Path
    read_only: bool


class WorkspaceResolver:
    """Ne résout que des identifiants préenregistrés, jamais un chemin réseau libre."""

    def __init__(self, workspaces: tuple[WorkspaceConfig, ...] | list[WorkspaceConfig]) -> None:
        registered: dict[str, RegisteredWorkspace] = {}
        for workspace in workspaces:
            try:
                root = workspace.root.resolve(strict=True)
            except OSError as exc:
                raise WorkspaceAccessError(
                    f"La racine du workspace {workspace.id!r} n'existe pas"
                ) from exc
            if not root.is_dir():
                raise WorkspaceAccessError(
                    f"La racine du workspace {workspace.id!r} n'est pas un dossier"
                )
            registered[workspace.id] = RegisteredWorkspace(
                id=workspace.id,
                display_name=workspace.display_name,
                root=root,
                read_only=workspace.read_only,
            )
        self._workspaces = registered

    def get(self, workspace_id: str) -> RegisteredWorkspace:
        try:
            return self._workspaces[workspace_id]
        except KeyError as exc:
            raise WorkspaceAccessError(f"Workspace inconnu: {workspace_id!r}") from exc

    def resolve(
        self,
        workspace_id: str,
        relative_path: str | Path = ".",
        *,
        require_write: bool = False,
        must_exist: bool = False,
    ) -> Path:
        workspace = self.get(workspace_id)
        if require_write and workspace.read_only:
            raise WorkspaceAccessError(f"Le workspace {workspace_id!r} est en lecture seule")
        relative = Path(relative_path)
        if relative.is_absolute():
            raise WorkspaceAccessError("Un chemin absolu n'est jamais accepté par le worker")
        try:
            candidate = (workspace.root / relative).resolve(strict=must_exist)
        except OSError as exc:
            raise WorkspaceAccessError("Le chemin demandé n'existe pas") from exc
        if not candidate.is_relative_to(workspace.root):
            raise WorkspaceAccessError("Le chemin sort de la racine canonique du workspace")
        return candidate

    def inventory(self) -> tuple[RegisteredWorkspace, ...]:
        return tuple(self._workspaces.values())
