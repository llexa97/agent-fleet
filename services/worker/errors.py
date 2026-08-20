"""Erreurs explicites du worker, sans données sensibles."""


class WorkerError(Exception):
    """Erreur de base du worker."""


class ConfigurationError(WorkerError):
    """Configuration locale invalide ou incomplète."""


class WorkspaceAccessError(WorkerError):
    """Accès hors d'un workspace enregistré ou interdit en écriture."""


class HarnessError(WorkerError):
    """Erreur d'un processus ou d'un échange ACP."""


class HarnessUnavailableError(HarnessError):
    """Exécutable absent, non exécutable ou capacité épuisée."""


class UnsupportedCapabilityError(HarnessError):
    """Capacité ACP optionnelle non annoncée par le harness."""


class ProtocolError(WorkerError):
    """Message du protocole worker invalide ou incompatible."""


class RelayError(WorkerError):
    """Erreur du relais MCP local."""
