from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app import schemas
from app.db import get_db
from app.models import User
from app.routers.common import IdempotencyKeyHeader, run_mutation
from app.security import get_current_user
from app.services import items as svc

router = APIRouter(prefix="/items", tags=["items"])


def _detail_payload(item, role, user) -> dict:
    return svc.to_detail(item, role, user.id).model_dump(mode="json")


@router.get("", response_model=schemas.ItemPage)
def list_items(
    q: str | None = Query(None, max_length=200),
    team_id: int | None = None,
    status: list[str] = Query(default=[]),
    priority: list[str] = Query(default=[]),
    owner: str | None = Query(None, description="'me', 'none' or a user id"),
    view: Literal["mine", "unassigned", "needs_approval", "overdue"] | None = None,
    sort: Literal["attention", "recent"] = "attention",
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return svc.list_items(
        db,
        user,
        q=q,
        team_id=team_id,
        statuses=status,
        priorities=priority,
        owner=owner,
        view=view,
        sort=sort,
        limit=limit,
        cursor=cursor,
    )


@router.post("", status_code=201)
def create_item(
    payload: schemas.ItemCreate,
    request: Request,
    idempotency_key: IdempotencyKeyHeader = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def action():
        item, role = svc.create_item(db, user, payload)
        return 201, _detail_payload(item, role, user)

    return run_mutation(db, user, request, idempotency_key, payload.model_dump(mode="json"), action)


@router.get("/{item_id}", response_model=schemas.ItemDetail)
def get_item(item_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item, role = svc.get_item(db, user, item_id)
    return svc.to_detail(item, role, user.id)


@router.patch("/{item_id}")
def patch_item(
    item_id: int,
    payload: schemas.ItemPatch,
    request: Request,
    idempotency_key: IdempotencyKeyHeader = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def action():
        item, role = svc.update_fields(db, user, item_id, payload)
        return 200, _detail_payload(item, role, user)

    body = payload.model_dump(mode="json", exclude_unset=True)
    return run_mutation(db, user, request, idempotency_key, body, action)


@router.post("/{item_id}/claim")
def claim_item(
    item_id: int,
    request: Request,
    idempotency_key: IdempotencyKeyHeader = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def action():
        item, role, _ = svc.claim(db, user, item_id)
        return 200, _detail_payload(item, role, user)

    return run_mutation(db, user, request, idempotency_key, None, action)


@router.post("/{item_id}/release")
def release_item(
    item_id: int,
    request: Request,
    idempotency_key: IdempotencyKeyHeader = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def action():
        item, role, _ = svc.release(db, user, item_id)
        return 200, _detail_payload(item, role, user)

    return run_mutation(db, user, request, idempotency_key, None, action)


@router.post("/{item_id}/assign")
def assign_item(
    item_id: int,
    payload: schemas.AssignIn,
    request: Request,
    idempotency_key: IdempotencyKeyHeader = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def action():
        item, role = svc.assign(db, user, item_id, payload)
        return 200, _detail_payload(item, role, user)

    return run_mutation(db, user, request, idempotency_key, payload.model_dump(mode="json"), action)


@router.post("/{item_id}/transition")
def transition_item(
    item_id: int,
    payload: schemas.TransitionIn,
    request: Request,
    idempotency_key: IdempotencyKeyHeader = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def action():
        item, role = svc.transition(db, user, item_id, payload)
        return 200, _detail_payload(item, role, user)

    return run_mutation(db, user, request, idempotency_key, payload.model_dump(mode="json"), action)


@router.post("/{item_id}/comments", status_code=201)
def add_comment(
    item_id: int,
    payload: schemas.CommentIn,
    request: Request,
    idempotency_key: IdempotencyKeyHeader = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def action():
        ev = svc.add_comment(db, user, item_id, payload)
        return 201, svc.to_event(ev).model_dump(mode="json")

    return run_mutation(db, user, request, idempotency_key, payload.model_dump(mode="json"), action)


@router.get("/{item_id}/events", response_model=schemas.EventPage)
def list_events(
    item_id: int,
    cursor: int | None = Query(None, description="return events older than this event id"),
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return svc.list_events(db, user, item_id, cursor, limit)
