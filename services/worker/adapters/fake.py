"""Adaptateur du faux agent ACP déterministe de test."""

from packages.contracts.enums import HarnessType

from .acp_stdio import AcpStdioAdapter


class FakeAcpAdapter(AcpStdioAdapter):
    harness_type = HarnessType.FAKE
    adapter_name = "agent-fleet-fake-acp"
