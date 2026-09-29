"""Work item use-cases. Each function is one atomic business operation.

Services never commit. The router commits once at the end, so the item change, its history
event and the idempotency record land together or not at all.

Concurrency strategy per operation (see ENGINEERING_DECISIONS.md):
  claim     - a single conditional UPDATE (`... WHERE owner_id IS NULL`). No read-then-write gap.
  patch /
  assign /
  transition- optimistic concurrency: `UPDATE ... WHERE version = :expected`. Zero rows updated
              means the caller was looking at a stale copy -> 409, nothing is overwritten.
  release   - short row lock (SELECT ... FOR UPDATE) because its side effects (status reset)
              depend on the item's current status.
"""

import base64
import binascii
import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import false, func, or_, select, tuple_, update
from sqlalchemy.orm import Session

from app import schemas
from app import workflow as wf
from app.errors import ApiError
from app.models import ItemEvent, User, WorkItem
from app.permissions import at_least, can_modify, get_role, my_roles, require_role

VIEWS = ("mine", "unassigned", "needs_approval", "overdue")


# --------------------------------------------------------------------------- serialization


def _user_ref(u: User | None) -> schemas.UserRef | None:
    return None if u is None else schemas.UserRef(id=u.id, name=u.name)


def _ref_dict(u: User | None) -> dict[str, Any] | None:
    return None if u is None else {"id": u.id, "name": u.name}


def to_summary(item: WorkItem) -> schemas.ItemSummary:
    return schemas.ItemSummary(
        id=item.id,
        team_id=item.team_id,
        team_name=item.team.name,
        title=item.title,
        status=item.status,  # type: ignore[arg-type]
        priority=wf.PRIORITY_NAMES[item.priority],  # type: ignore[arg-type]
        owner=_user_ref(item.owner),
        created_by=_user_ref(item.creator),  # type: ignore[arg-type]
        due_at=item.due_at,
        created_at=item.created_at,
        updated_at=item.updated_at,
        version=item.version,
    )


def permissions_for(item: WorkItem, role: str, user_id: int) -> schemas.Permissions:
    is_done = item.status in wf.DONE
    transitions = [
        to
        for to in wf.TRANSITIONS[item.status]
        if wf.check_transition(
            status=item.status,
            owner_id=item.owner_id,
            created_by=item.created_by,
            role=role,
            user_id=user_id,
            to=to,
        )
        is None
    ]
    return schemas.Permissions(
        role=role,
        can_edit=can_modify(role, item.owner_id, item.created_by, user_id) and item.status != wf.CLOSED,
        can_claim=at_least(role, "member") and item.owner_id is None and item.status == wf.OPEN,
        can_release=item.owner_id == user_id and not is_done,
        can_assign=role == "manager" and not is_done,
        can_comment=at_least(role, "member"),
        transitions=transitions,  # type: ignore[arg-type]
    )


def to_detail(item: WorkItem, role: str, user_id: int) -> schemas.ItemDetail:
    summary = to_summary(item).model_dump()
    return schemas.ItemDetail(
        **summary, description=item.description, permissions=permissions_for(item, role, user_id)
    )


def to_event(ev: ItemEvent) -> schemas.EventOut:
    return schemas.EventOut(
        id=ev.id,
        item_id=ev.item_id,
        kind=ev.kind,
        actor=schemas.UserRef(id=ev.actor.id, name=ev.actor.name),
        data=ev.data,
        created_at=ev.created_at,
    )


# --------------------------------------------------------------------------- helpers


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def add_event(db: Session, item_id: int, actor: User, kind: str, data: dict[str, Any]) -> ItemEvent:
    ev = ItemEvent(item_id=item_id, actor=actor, kind=kind, data=data)
    db.add(ev)
    db.flush()
    return ev


def load_item(db: Session, user: User, item_id: int, minimum: str, action: str) -> tuple[WorkItem, str]:
    """Load an item and enforce resource-level authorization against ITS team."""
    item = db.get(WorkItem, item_id)
    role = get_role(db, user.id, item.team_id) if item is not None else None
    require_role(role, minimum, action)  # None -> 404 (existence not leaked), too low -> 403
    assert item is not None and role is not None
    return item, role


def _stale(current_version: int) -> ApiError:
    return ApiError(
        409,
        "stale_version",
        "This item was changed by someone else since you loaded it. Reload to see the latest version.",
        {"current_version": current_version},
    )


def _versioned_update(db: Session, item: WorkItem, expected_version: int, values: dict[str, Any]) -> None:
    """Apply `values` only if the row is still at `expected_version`; bump the version."""
    result = db.execute(
        update(WorkItem)
        .where(WorkItem.id == item.id, WorkItem.version == expected_version)
        .values(**values, version=WorkItem.version + 1, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    db.refresh(item)
    if result.rowcount != 1:
        raise _stale(item.version)


# --------------------------------------------------------------------------- commands


def create_item(db: Session, user: User, payload: schemas.ItemCreate) -> tuple[WorkItem, str]:
    role = get_role(db, user.id, payload.team_id)
    require_role(role, "member", "create items in this team")
    assert role is not None
    item = WorkItem(
        team_id=payload.team_id,
        title=payload.title,
        description=payload.description,
        priority=wf.PRIORITIES[payload.priority],
        status=wf.OPEN,
        created_by=user.id,
        due_at=payload.due_at,
    )
    db.add(item)
    db.flush()
    add_event(db, item.id, user, "created", {"title": item.title, "priority": payload.priority})
    db.refresh(item)
    return item, role


def update_fields(db: Session, user: User, item_id: int, payload: schemas.ItemPatch) -> tuple[WorkItem, str]:
    item, role = load_item(db, user, item_id, "viewer", "view this item")
    if not can_modify(role, item.owner_id, item.created_by, user.id):
        raise ApiError(403, "forbidden", "Only the owner, the creator or a team manager can edit this item")
    if item.status == wf.CLOSED:
        raise ApiError(409, "item_closed", "Closed items cannot be edited; reopen the item first")
    if item.version != payload.expected_version:
        raise _stale(item.version)

    sent = payload.model_fields_set
    changes: dict[str, dict[str, Any]] = {}
    values: dict[str, Any] = {}

    if "title" in sent and payload.title is not None:
        title = payload.title.strip()
        if not title:
            raise ApiError(422, "validation_error", "title must not be blank")
        if title != item.title:
            changes["title"] = {"from": item.title, "to": title}
            values["title"] = title
    if "description" in sent and payload.description is not None and payload.description != item.description:
        changes["description"] = {"from": item.description, "to": payload.description}
        values["description"] = payload.description
    if "priority" in sent and payload.priority is not None:
        new_priority = wf.PRIORITIES[payload.priority]
        if new_priority != item.priority:
            changes["priority"] = {"from": wf.PRIORITY_NAMES[item.priority], "to": payload.priority}
            values["priority"] = new_priority
    if "due_at" in sent and payload.due_at != item.due_at:
        changes["due_at"] = {"from": _iso(item.due_at), "to": _iso(payload.due_at)}
        values["due_at"] = payload.due_at

    if not changes:
        return item, role  # nothing to do: no version bump, no noise in history

    _versioned_update(db, item, payload.expected_version, values)
    add_event(db, item.id, user, "updated", {"changes": changes})
    return item, role


def claim(db: Session, user: User, item_id: int) -> tuple[WorkItem, str, bool]:
    """Take ownership of an unowned open item and start work. Exactly one concurrent caller wins."""
    item, role = load_item(db, user, item_id, "member", "claim items")
    result = db.execute(
        update(WorkItem)
        .where(WorkItem.id == item_id, WorkItem.owner_id.is_(None), WorkItem.status == wf.OPEN)
        .values(
            owner_id=user.id,
            status=wf.IN_PROGRESS,
            version=WorkItem.version + 1,
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    db.refresh(item)
    if result.rowcount == 1:
        add_event(db, item.id, user, "assigned", {"from": None, "to": _ref_dict(user), "via": "claim"})
        add_event(
            db, item.id, user, "status_changed", {"from": wf.OPEN, "to": wf.IN_PROGRESS, "note": "claimed"}
        )
        return item, role, True

    # Lost the race (or the item is not claimable). Explain precisely why.
    if item.owner_id == user.id:
        return item, role, False  # already mine: repeating the action is a harmless no-op
    if item.owner_id is not None:
        raise ApiError(
            409,
            "already_claimed",
            f"{item.owner.name if item.owner else 'Someone else'} has already claimed this item",
            {"owner": _ref_dict(item.owner), "current_version": item.version},
        )
    raise ApiError(409, "not_claimable", f"An item that is {item.status} cannot be claimed")


def release(db: Session, user: User, item_id: int) -> tuple[WorkItem, str, bool]:
    """Give up ownership. Work in progress goes back to `open` so the invariant
    'active work has an owner' still holds."""
    _, role = load_item(db, user, item_id, "member", "release items")
    item = db.execute(
        select(WorkItem)
        .where(WorkItem.id == item_id)
        .with_for_update(of=WorkItem)
        .execution_options(populate_existing=True)
    ).scalar_one()

    if item.owner_id is None:
        return item, role, False  # already released
    if item.owner_id != user.id:
        raise ApiError(403, "not_owner", "Only the current owner can release an item; ask a manager to reassign it")
    if item.status in wf.DONE:
        raise ApiError(409, "item_done", f"A {item.status} item cannot be released")

    old_status = item.status
    new_status = wf.OPEN if old_status in (wf.IN_PROGRESS, wf.PENDING_APPROVAL) else old_status
    db.execute(
        update(WorkItem)
        .where(WorkItem.id == item_id)
        .values(owner_id=None, status=new_status, version=WorkItem.version + 1, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    db.refresh(item)
    add_event(db, item.id, user, "assigned", {"from": _ref_dict(user), "to": None, "via": "release"})
    if new_status != old_status:
        add_event(
            db,
            item.id,
            user,
            "status_changed",
            {"from": old_status, "to": new_status, "note": "owner released the item"},
        )
    return item, role, True


def assign(db: Session, user: User, item_id: int, payload: schemas.AssignIn) -> tuple[WorkItem, str]:
    item, role = load_item(db, user, item_id, "manager", "assign items")
    if item.version != payload.expected_version:
        raise _stale(item.version)
    if item.status in wf.DONE:
        raise ApiError(409, "item_done", f"A {item.status} item cannot be reassigned")
    if payload.user_id == item.owner_id:
        return item, role

    target: User | None = None
    if payload.user_id is not None:
        if not at_least(get_role(db, payload.user_id, item.team_id), "member"):
            raise ApiError(
                422, "invalid_assignee", "The assignee must be a member of this team (viewers cannot own work)"
            )
        target = db.get(User, payload.user_id)

    old_owner = _ref_dict(item.owner)
    old_status = item.status
    new_status = wf.OPEN if target is None and old_status in (wf.IN_PROGRESS, wf.PENDING_APPROVAL) else old_status
    _versioned_update(
        db, item, payload.expected_version, {"owner_id": payload.user_id, "status": new_status}
    )
    add_event(db, item.id, user, "assigned", {"from": old_owner, "to": _ref_dict(target), "via": "assign"})
    if new_status != old_status:
        add_event(
            db, item.id, user, "status_changed", {"from": old_status, "to": new_status, "note": "unassigned"}
        )
    return item, role


def transition(db: Session, user: User, item_id: int, payload: schemas.TransitionIn) -> tuple[WorkItem, str]:
    item, role = load_item(db, user, item_id, "viewer", "view this item")
    if item.version != payload.expected_version:
        raise _stale(item.version)
    err = wf.check_transition(
        status=item.status,
        owner_id=item.owner_id,
        created_by=item.created_by,
        role=role,
        user_id=user.id,
        to=payload.to,
    )
    if err is not None:
        raise err
    old_status = item.status
    _versioned_update(db, item, payload.expected_version, {"status": payload.to})
    add_event(db, item.id, user, "status_changed", {"from": old_status, "to": payload.to, "note": payload.note})
    return item, role


def add_comment(db: Session, user: User, item_id: int, payload: schemas.CommentIn) -> ItemEvent:
    item, _ = load_item(db, user, item_id, "member", "comment on items")
    ev = add_event(db, item.id, user, "comment", {"body": payload.body})
    # A comment is activity (surfaces the item) but does not change the item's own fields,
    # so it must NOT bump `version` - otherwise commenting would break other people's edits.
    db.execute(update(WorkItem).where(WorkItem.id == item.id).values(updated_at=func.now()))
    return ev


# --------------------------------------------------------------------------- queries


def get_item(db: Session, user: User, item_id: int) -> tuple[WorkItem, str]:
    return load_item(db, user, item_id, "viewer", "view this item")


def list_events(
    db: Session, user: User, item_id: int, before_id: int | None, limit: int
) -> schemas.EventPage:
    load_item(db, user, item_id, "viewer", "view this item")
    stmt = select(ItemEvent).where(ItemEvent.item_id == item_id).order_by(ItemEvent.id.desc()).limit(limit + 1)
    if before_id is not None:
        stmt = stmt.where(ItemEvent.id < before_id)
    rows = list(db.execute(stmt).scalars())
    page = rows[:limit]
    next_cursor = page[-1].id if len(rows) > limit and page else None
    return schemas.EventPage(events=[to_event(e) for e in page], next_cursor=next_cursor)


def _view_conditions(view: str, user_id: int, roles: dict[int, str]) -> list[Any]:
    if view == "mine":
        return [WorkItem.owner_id == user_id, WorkItem.status.in_(wf.ACTIVE)]
    if view == "unassigned":
        return [WorkItem.owner_id.is_(None), WorkItem.status == wf.OPEN]
    if view == "needs_approval":
        managed = [t for t, r in roles.items() if r == "manager"]
        return [WorkItem.status == wf.PENDING_APPROVAL, WorkItem.team_id.in_(managed) if managed else false()]
    if view == "overdue":
        return [WorkItem.due_at < func.now(), WorkItem.status.in_(wf.ACTIVE)]
    raise ApiError(422, "validation_error", f"Unknown view '{view}'")


def _encode_cursor(parts: list[Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(parts).encode()).decode()


def _decode_cursor(cursor: str, expected_len: int) -> list[Any]:
    try:
        parts = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        assert isinstance(parts, list) and len(parts) == expected_len
        # layout is [priority?, updated_at, id]; updated_at is always second to last
        parts[-2] = datetime.fromisoformat(parts[-2])
        assert isinstance(parts[-1], int) and all(isinstance(p, int) for p in parts[:-2])
        return parts
    except (binascii.Error, ValueError, AssertionError, TypeError, json.JSONDecodeError):
        raise ApiError(400, "bad_cursor", "Invalid pagination cursor")


def list_items(
    db: Session,
    user: User,
    *,
    q: str | None,
    team_id: int | None,
    statuses: list[str],
    priorities: list[str],
    owner: str | None,
    view: str | None,
    sort: str,
    limit: int,
    cursor: str | None,
) -> schemas.ItemPage:
    roles = my_roles(db, user.id)
    if not roles:
        return schemas.ItemPage(items=[], next_cursor=None)

    # Authorization is part of the query itself: a user can only ever see their teams' items.
    conds: list[Any] = []
    if team_id is not None:
        if team_id not in roles:
            raise ApiError(404, "not_found", "Not found")
        conds.append(WorkItem.team_id == team_id)
    else:
        conds.append(WorkItem.team_id.in_(list(roles)))

    if statuses:
        bad = [s for s in statuses if s not in wf.STATUSES]
        if bad:
            raise ApiError(422, "validation_error", f"Unknown status '{bad[0]}'")
        conds.append(WorkItem.status.in_(statuses))
    if priorities:
        bad = [p for p in priorities if p not in wf.PRIORITIES]
        if bad:
            raise ApiError(422, "validation_error", f"Unknown priority '{bad[0]}'")
        conds.append(WorkItem.priority.in_([wf.PRIORITIES[p] for p in priorities]))
    if owner == "me":
        conds.append(WorkItem.owner_id == user.id)
    elif owner == "none":
        conds.append(WorkItem.owner_id.is_(None))
    elif owner:
        try:
            conds.append(WorkItem.owner_id == int(owner))
        except ValueError:
            raise ApiError(422, "validation_error", "owner must be 'me', 'none' or a user id")
    if view:
        conds.extend(_view_conditions(view, user.id, roles))

    if q and q.strip():
        tokens = re.findall(r"\w+", q, re.UNICODE)[:8]
        if tokens:
            ts_query = " & ".join(f"{t}:*" for t in tokens)  # prefix match: "paym" finds "payment"
            match = WorkItem.search.op("@@")(func.to_tsquery("english", ts_query))
            if len(tokens) == 1 and tokens[0].isdigit() and len(tokens[0]) <= 9:
                match = or_(match, WorkItem.id == int(tokens[0]))  # "123" also finds item #123
            conds.append(match)

    if sort == "recent":
        order_cols = [WorkItem.updated_at, WorkItem.id]
    else:
        order_cols = [WorkItem.priority, WorkItem.updated_at, WorkItem.id]

    stmt = select(WorkItem).where(*conds)
    if cursor:
        values = _decode_cursor(cursor, len(order_cols))
        stmt = stmt.where(tuple_(*order_cols) < tuple_(*values))  # keyset: no OFFSET, stable under inserts
    stmt = stmt.order_by(*[c.desc() for c in order_cols]).limit(limit + 1)

    rows = list(db.execute(stmt).unique().scalars())
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        parts = [last.priority] if sort != "recent" else []
        parts += [last.updated_at.isoformat(), last.id]
        next_cursor = _encode_cursor(parts)
    return schemas.ItemPage(items=[to_summary(i) for i in page], next_cursor=next_cursor)


def dashboard(db: Session, user: User) -> schemas.DashboardOut:
    from app.models import Membership, Team

    roles = my_roles(db, user.id)
    if not roles:
        return schemas.DashboardOut(counts={v: 0 for v in VIEWS}, by_status={}, teams=[])
    counts = {
        view: db.execute(
            select(func.count()).select_from(WorkItem).where(
                WorkItem.team_id.in_(list(roles)), *_view_conditions(view, user.id, roles)
            )
        ).scalar_one()
        for view in VIEWS
    }
    by_status = dict(
        db.execute(
            select(WorkItem.status, func.count()).where(WorkItem.team_id.in_(list(roles))).group_by(WorkItem.status)
        ).all()
    )
    teams = db.execute(
        select(Membership.team_id, Team.name, Membership.role)
        .join(Team, Team.id == Membership.team_id)
        .where(Membership.user_id == user.id)
        .order_by(Team.name)
    ).all()
    return schemas.DashboardOut(
        counts=counts,
        by_status=by_status,
        teams=[schemas.Membership(team_id=t, team_name=n, role=r) for t, n, r in teams],
    )
