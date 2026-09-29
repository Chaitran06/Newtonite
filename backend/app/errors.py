from typing import Any


class ApiError(Exception):
    """Domain error with a stable machine-readable `code` the frontend can branch on."""

    def __init__(self, status: int, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, **self.extra}}
