"""Critical behaviour #3: a user repeats an action because they are unsure the first one worked."""

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ItemEvent, WorkItem
from tests.conftest import run_concurrently


def _count(model):
    with SessionLocal() as db:
        return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_same_key_creates_only_one_item_and_replays_the_response(client, world):
    body = {"team_id": world.team["support"], "title": "Refund not processed"}
    h = {**world.h("bob"), "Idempotency-Key": "create-1"}
    first = client.post("/items", json=body, headers=h)
    second = client.post("/items", json=body, headers=h)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert second.headers.get("idempotent-replay") == "true"
    assert _count(WorkItem) == 1
    assert _count(ItemEvent) == 1  # one 'created' event, not two


def test_concurrent_requests_with_the_same_key_execute_once(client, world):
    body = {"team_id": world.team["support"], "title": "Double click"}
    h = {**world.h("bob"), "Idempotency-Key": "race-1"}
    results = run_concurrently(8, lambda i: client.post("/items", json=body, headers=h))
    assert {r.status_code for r in results} == {201}
    assert len({r.json()["id"] for r in results}) == 1
    assert _count(WorkItem) == 1


def test_duplicate_comment_submission_posts_one_comment(client, world, mk_item):
    item = mk_item()
    h = {**world.h("carol"), "Idempotency-Key": "comment-1"}
    for _ in range(3):
        assert client.post(f"/items/{item['id']}/comments", json={"body": "on it"}, headers=h).status_code == 201
    events = client.get(f"/items/{item['id']}/events", headers=world.h("carol")).json()["events"]
    assert [e["kind"] for e in events].count("comment") == 1


def test_reusing_a_key_for_a_different_request_is_rejected(client, world):
    h = {**world.h("bob"), "Idempotency-Key": "k"}
    assert client.post("/items", json={"team_id": world.team["support"], "title": "A"}, headers=h).status_code == 201
    r = client.post("/items", json={"team_id": world.team["support"], "title": "B"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "idempotency_key_reuse"
    assert _count(WorkItem) == 1


def test_failed_attempt_does_not_burn_the_key(client, world, mk_item):
    item = mk_item(as_="bob")
    h = {**world.h("dave"), "Idempotency-Key": "retry-me"}  # dave is a viewer -> forbidden
    assert client.post(f"/items/{item['id']}/comments", json={"body": "hi"}, headers=h).status_code == 403
    # The same key from the same user can be used again once the situation is fixed; nothing was stored.
    with SessionLocal() as db:
        from app.models import IdempotencyKey
        assert db.execute(select(func.count()).select_from(IdempotencyKey)).scalar_one() == 0


def test_keys_are_scoped_per_user(client, world):
    body = {"team_id": world.team["support"], "title": "Same key, different people"}
    a = client.post("/items", json=body, headers={**world.h("bob"), "Idempotency-Key": "shared"})
    b = client.post("/items", json=body, headers={**world.h("carol"), "Idempotency-Key": "shared"})
    assert a.status_code == b.status_code == 201 and a.json()["id"] != b.json()["id"]
