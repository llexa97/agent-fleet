"""Adaptateur du serveur ACP natif d'OpenCode."""

from packages.contracts.enums import HarnessType

from .acp_stdio import AcpStdioAdapter


class OpenCodeAcpAdapter(AcpStdioAdapter):
    harness_type = HarnessType.OPENCODE
    adapter_name = "opencode-acp"
