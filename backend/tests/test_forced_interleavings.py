"""Deterministic race tests.

Thread-based tests (elsewhere) show the system behaves under real parallel load, but a race
window can be missed by luck. These tests FORCE the dangerous interleaving with two open
transactions, so they fail every time if a guard is removed.
"""

import threading
import time

import pytest
from sqlalchemy import select

from app import idempotency, schemas
from app.db import SessionLocal
from app.errors import ApiError
from app.models import User, WorkItem
from app.services import items as svc


def test_version_guard_blocks_a_write_based_on_a_copy_that_went_stale_mid_request(world, mk_item):
    item = mk_item(as_="bob", title="Original")
    v = item["version"]

    with SessionLocal() as a, SessionLocal() as b:
        # Request A has loaded the item and passed all its checks at version v...
        item_a = a.get(WorkItem, item["id"])
        assert item_a.version == v

        # ...meanwhile request B edits the same item and commits.
        user_b = b.get(User, world.u["alice"])
        svc.update_fields(b, user_b, item["id"], schemas.ItemPatch(expected_version=v, title="B's edit"))
        b.commit()

        # A now issues its UPDATE. The WHERE version = v guard must refuse it.
        with pytest.raises(ApiError) as exc:
            svc._versioned_update(a, item_a, v, {"title": "A's edit"})
        assert exc.value.code == "stale_version" and exc.value.status == 409

    with SessionLocal() as db:
        assert db.get(WorkItem, item["id"]).title == "B's edit"  # B's write survived


def test_claim_is_atomic_even_if_the_other_claim_commits_between_load_and_update(world, mk_item):
    item = mk_item(as_="bob")
    with SessionLocal() as a, SessionLocal() as b:
        # A has loaded the item as unowned (this is what a read-then-write implementation would trust).
        assert a.get(WorkItem, item["id"]).owner_id is None
        # B claims and commits first.
        svc.claim(b, b.get(User, world.u["carol"]), item["id"])
        b.commit()
        # A's claim must lose, despite having seen "unowned" earlier.
        with pytest.raises(ApiError) as exc:
            svc.claim(a, a.get(User, world.u["bob"]), item["id"])
        assert exc.value.code == "already_claimed"


def test_idempotency_second_request_waits_for_the_first_then_replays(world):
    uid = world.u["bob"]
    outcome: dict = {}

    with SessionLocal() as a:
        replay, pending = idempotency.begin(a, uid, "k1", "hash")  # A owns the key, NOT committed yet
        assert replay is None and pending is not None

        def request_b():
            with SessionLocal() as b:
                outcome["b"] = idempotency.begin(b, uid, "k1", "hash")

        t = threading.Thread(target=request_b)
        t.start()
        time.sleep(0.6)
        assert t.is_alive(), "B must be blocked while A's transaction is still open"

        idempotency.finish(a, pending, 201, {"id": 42})
        a.commit()  # only now does B get to see the key

    t.join(timeout=5)
    replay_b, pending_b = outcome["b"]
    assert pending_b is None and replay_b is not None
    assert replay_b.status_code == 201 and replay_b.response == {"id": 42}


def test_idempotency_if_the_first_request_rolls_back_the_second_may_proceed(world):
    uid = world.u["bob"]
    outcome: dict = {}

    with SessionLocal() as a:
        _, pending = idempotency.begin(a, uid, "k2", "hash")
        assert pending is not None

        def request_b():
            with SessionLocal() as b:
                outcome["b"] = idempotency.begin(b, uid, "k2", "hash")
                b.rollback()

        t = threading.Thread(target=request_b)
        t.start()
        time.sleep(0.4)
        a.rollback()  # A failed (e.g. validation error) -> its key never existed

    t.join(timeout=5)
    replay_b, pending_b = outcome["b"]
    assert replay_b is None and pending_b is not None  # B is a fresh first attempt
