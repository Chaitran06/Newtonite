"""Idempotency keys.

Client sends `Idempotency-Key: <uuid>` on a mutating request. If the request is repeated
(double click, timeout + retry, flaky network) the server replays the stored response
instead of performing the action twice.

How it stays correct under concurrency:
  * The key row is inserted in the SAME transaction as the business change. It only becomes
    visible (commits) together with the change and the stored response.
  * A concurrent request with the same key blocks on the unique index until the first
    transaction finishes. If the first commits, the second sees the conflict and replays.
    If the first rolls back (e.g. validation error), the second simply proceeds.
  * The key is bound to a hash of (method, path, body): reusing a key for a different
    request is rejected instead of silently returning an unrelated response.
Only successful (2xx) responses are stored; a failed attempt leaves no trace and can be retried.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import IdempotencyKey


@dataclass
class Pending:
    row_id: int


@dataclass
class Replay:
    status_code: int
    response: Any


def request_hash(method: str, path: str, body: Any) -> str:
    canonical = json.dumps({"m": method, "p": path, "b": body}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def begin(db: Session, user_id: int, key: str, req_hash: str) -> tuple[Replay | None, Pending | None]:
    row_id = db.execute(
        pg_insert(IdempotencyKey)
        .values(user_id=user_id, key=key, request_hash=req_hash)
        .on_conflict_do_nothing(constraint="uq_idempotency_user_key")
        .returning(IdempotencyKey.id)
    ).scalar_one_or_none()
    if row_id is not None:
        return None, Pending(row_id)  # we own this key

    existing = db.execute(
        select(IdempotencyKey).where(IdempotencyKey.user_id == user_id, IdempotencyKey.key == key)
    ).scalar_one()
    if existing.request_hash != req_hash:
        raise ApiError(
            422,
            "idempotency_key_reuse",
            "This Idempotency-Key was already used for a different request",
        )
    return Replay(existing.status_code or 200, existing.response), None


def finish(db: Session, pending: Pending, status_code: int, response: Any) -> None:
    db.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.id == pending.row_id)
        .values(status_code=status_code, response=response)
    )
