"""Contrats versionnés partagés entre le Control Plane et les workers."""

from packages.contracts.events import EventEnvelope
from packages.contracts.worker_protocol import PROTOCOL_VERSION, WireEnvelope

__all__ = ["PROTOCOL_VERSION", "EventEnvelope", "WireEnvelope"]
