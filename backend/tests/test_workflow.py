"""Critical behaviour #5: workflow rules are enforced by the server, not by the UI."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import engine
from app.workflow import TRANSITIONS, check_transition


def _transition(client, world, item, to, as_, note=None):
    body = {"expected_version": item["version"], "to": to}
    if note:
        body["note"] = note
    return client.post(f"/items/{item['id']}/transition", json=body, headers=world.h(as_))


def _current(client, world, item, as_="alice"):
    return client.get(f"/items/{item['id']}", headers=world.h(as_)).json()


def test_cannot_start_work_without_an_owner(client, world, mk_item):
    item = mk_item(as_="bob")
    r = _transition(client, world, item, "in_progress", "alice")
    assert r.status_code == 409 and r.json()["error"]["code"] == "owner_required"


def test_illegal_jumps_are_rejected(client, world, mk_item):
    item = mk_item(as_="bob")
    r = _transition(client, world, item, "resolved", "alice")  # open -> resolved is not a legal move
    assert r.status_code == 409 and r.json()["error"]["code"] == "illegal_transition"


def test_approval_flow_member_requests_manager_decides(client, world, mk_item):
    item = mk_item(as_="bob")
    client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))
    item = _current(client, world, item, "bob")

    req = _transition(client, world, item, "pending_approval", "bob", note="needs sign-off")
    assert req.status_code == 200
    item = req.json()

    # The requester cannot approve their own work, nor pull it back out of the queue.
    assert _transition(client, world, item, "resolved", "bob").status_code == 403
    assert _transition(client, world, item, "in_progress", "bob").status_code == 403

    approved = _transition(client, world, item, "resolved", "alice", note="approved")
    assert approved.status_code == 200 and approved.json()["status"] == "resolved"

    events = client.get(f"/items/{item['id']}/events", headers=world.h("alice")).json()["events"]
    status_events = [e for e in events if e["kind"] == "status_changed"]
    # newest first: approval (by alice, with the reason), request (by bob), claim
    assert status_events[0]["actor"]["name"] == "Alice" and status_events[0]["data"]["note"] == "approved"
    assert status_events[1]["actor"]["name"] == "Bob" and status_events[1]["data"]["to"] == "pending_approval"


def test_member_cannot_resolve_directly_only_managers(client, world, mk_item):
    item = mk_item(as_="bob")
    client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))
    item = _current(client, world, item, "bob")
    r = _transition(client, world, item, "resolved", "bob")
    assert r.status_code == 403 and r.json()["error"]["code"] == "approval_required"


def test_releasing_in_progress_work_puts_it_back_in_the_queue(client, world, mk_item):
    item = mk_item(as_="bob")
    client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))
    released = client.post(f"/items/{item['id']}/release", headers=world.h("bob")).json()
    assert released["owner"] is None and released["status"] == "open"


def test_unassigning_in_progress_work_also_resets_status(client, world, mk_item):
    item = mk_item(as_="bob")
    client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))
    item = _current(client, world, item)
    r = client.post(f"/items/{item['id']}/assign", json={"expected_version": item["version"], "user_id": None},
                    headers=world.h("alice"))
    assert r.status_code == 200 and r.json()["status"] == "open" and r.json()["owner"] is None


def test_closed_items_are_frozen_until_a_manager_reopens(client, world, mk_item):
    item = mk_item(as_="bob")
    closed = _transition(client, world, item, "closed", "bob").json()
    edit = client.patch(f"/items/{item['id']}", json={"expected_version": closed["version"], "title": "x"},
                        headers=world.h("bob"))
    assert edit.status_code == 409 and edit.json()["error"]["code"] == "item_closed"
    assert _transition(client, world, closed, "open", "bob").status_code == 403  # member cannot reopen
    assert _transition(client, world, closed, "open", "alice").status_code == 200


def test_comments_still_allowed_on_closed_items(client, world, mk_item):
    item = mk_item(as_="bob")
    _transition(client, world, item, "closed", "bob")
    r = client.post(f"/items/{item['id']}/comments", json={"body": "follow-up for the record"},
                    headers=world.h("carol"))
    assert r.status_code == 201


def test_database_itself_refuses_active_work_without_owner(world, mk_item):
    """Defence in depth: even a buggy code path cannot create ownerless in-progress work."""
    item = mk_item(as_="bob")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE work_items SET status='in_progress' WHERE id=:i"), {"i": item["id"]})


def test_every_transition_target_is_a_known_status():
    for src, targets in TRANSITIONS.items():
        for t in targets:
            assert t in TRANSITIONS, (src, t)


def test_check_transition_is_pure_and_consistent():
    err = check_transition(status="open", owner_id=None, created_by=1, role="manager", user_id=2, to="in_progress")
    assert err is not None and err.code == "owner_required"
    ok = check_transition(status="open", owner_id=2, created_by=1, role="member", user_id=2, to="in_progress")
    assert ok is None
