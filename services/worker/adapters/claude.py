"""Adaptateur du serveur ACP officiel Claude Agent."""

from packages.contracts.enums import HarnessType

from .acp_stdio import AcpStdioAdapter


class ClaudeAcpAdapter(AcpStdioAdapter):
    harness_type = HarnessType.CLAUDE
    adapter_name = "claude-agent-acp"
