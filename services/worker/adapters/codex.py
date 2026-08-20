"""Adaptateur du serveur ACP officiel Codex."""

from packages.contracts.enums import HarnessType

from .acp_stdio import AcpStdioAdapter


class CodexAcpAdapter(AcpStdioAdapter):
    harness_type = HarnessType.CODEX
    adapter_name = "codex-acp"
