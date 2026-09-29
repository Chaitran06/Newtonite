from typing import Annotated, Any, Callable

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import idempotency
from app.errors import ApiError
from app.models import User

IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)]


def run_mutation(
    db: Session,
    user: User,
    request: Request,
    key: str | None,
    body: Any,
    action: Callable[[], tuple[int, Any]],
) -> JSONResponse:
    """Run a state-changing use-case exactly once per Idempotency-Key and commit.

    `action` performs the writes (without committing) and returns (status_code, json_payload).
    The business change, its history event and the idempotency record are committed together.
    """
    pending = None
    if key:
        req_hash = idempotency.request_hash(request.method, request.url.path, body)
        replay, pending = idempotency.begin(db, user.id, key, req_hash)
        if replay is not None:
            db.rollback()  # nothing to write; release the connection cleanly
            return JSONResponse(
                replay.response, status_code=replay.status_code, headers={"Idempotent-Replay": "true"}
            )

    status_code, payload = action()
    if pending is not None:
        idempotency.finish(db, pending, status_code, payload)
    db.commit()
    return JSONResponse(payload, status_code=status_code)


__all__ = ["ApiError", "IdempotencyKeyHeader", "run_mutation"]
