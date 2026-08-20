from typing import Any


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class NotFoundError(DomainError):
    def __init__(self, resource: str, resource_id: object) -> None:
        super().__init__(
            "not_found",
            f"{resource} introuvable",
            status_code=404,
            details={"resource": resource, "id": str(resource_id)},
        )


class ForbiddenError(DomainError):
    def __init__(self, message: str = "Accès refusé", *, code: str = "forbidden") -> None:
        super().__init__(code, message, status_code=403)


class ConflictError(DomainError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, status_code=409, details=details)
