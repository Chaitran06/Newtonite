"""History must record what happened, and listing must stay correct while data grows and changes."""

from tests.conftest import run_concurrently


def test_every_important_action_leaves_an_attributed_history_entry(client, world, mk_item):
    item = mk_item(as_="bob", title="Original title")
    iid = item["id"]
    client.patch(f"/items/{iid}", json={"expected_version": item["version"], "priority": "urgent",
                                        "title": "New title"}, headers=world.h("alice"))
    client.post(f"/items/{iid}/claim", headers=world.h("carol"))
    client.post(f"/items/{iid}/comments", json={"body": "on it"}, headers=world.h("carol"))

    events = client.get(f"/items/{iid}/events", headers=world.h("dave")).json()["events"]  # viewers can read history
    kinds = [e["kind"] for e in events]
    assert kinds == ["comment", "status_changed", "assigned", "updated", "created"]  # newest first

    updated = next(e for e in events if e["kind"] == "updated")
    assert updated["actor"]["name"] == "Alice"
    assert updated["data"]["changes"]["priority"] == {"from": "medium", "to": "urgent"}
    assert updated["data"]["changes"]["title"] == {"from": "Original title", "to": "New title"}


def test_history_cannot_be_edited_or_deleted_through_the_api(client, world, mk_item):
    item = mk_item()
    for method in ("put", "patch", "delete"):
        r = getattr(client, method)(f"/items/{item['id']}/events", headers=world.h("alice"))
        assert r.status_code in (404, 405)


def test_history_is_paginated_with_a_cursor(client, world, mk_item):
    item = mk_item()
    for n in range(12):
        client.post(f"/items/{item['id']}/comments", json={"body": f"c{n}"}, headers=world.h("bob"))
    seen, cursor = [], None
    while True:
        url = f"/items/{item['id']}/events?limit=5" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=world.h("bob")).json()
        seen += [e["id"] for e in page["events"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 13 and len(set(seen)) == 13 and seen == sorted(seen, reverse=True)


def test_keyset_pagination_has_no_gaps_or_duplicates_even_while_new_items_arrive(client, world, mk_item):
    original = {mk_item(as_="bob", title=f"Ticket {n}", priority=["low", "medium", "high", "urgent"][n % 4])["id"]
                for n in range(23)}
    seen, cursor, first_page = [], None, True
    while True:
        url = "/items?limit=5&sort=attention" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(url, headers=world.h("carol")).json()
        seen += [i["id"] for i in page["items"]]
        if first_page:  # new work arrives between page loads; OFFSET pagination would now repeat rows
            mk_item(as_="bob", title="Urgent, arrived mid-scroll", priority="urgent")  # sorts BEFORE the cursor
            mk_item(as_="bob", title="Low, arrived mid-scroll", priority="low")  # sorts AFTER the cursor
            first_page = False
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len(set(seen)), "an item was returned twice"
    assert original <= set(seen), "an item that existed the whole time was skipped"


def test_attention_sort_puts_urgent_first(client, world, mk_item):
    mk_item(as_="bob", title="low one", priority="low")
    mk_item(as_="bob", title="urgent one", priority="urgent")
    mk_item(as_="bob", title="high one", priority="high")
    titles = [i["title"] for i in client.get("/items", headers=world.h("bob")).json()["items"]]
    assert titles == ["urgent one", "high one", "low one"]


def test_search_supports_prefixes_and_ids_and_is_scoped_to_my_teams(client, world, mk_item):
    a = mk_item(as_="bob", title="Refund request not processed", description="customer waiting")
    mk_item(as_="bob", title="Login broken")
    mk_item(as_="erin", team="eng", title="Refund service deploy failing")  # other team: must not leak
    found = client.get("/items?q=refu", headers=world.h("bob")).json()["items"]
    assert [i["id"] for i in found] == [a["id"]]
    assert [i["id"] for i in client.get("/items?q=customer waiting", headers=world.h("bob")).json()["items"]] == [a["id"]]
    assert [i["id"] for i in client.get(f"/items?q={a['id']}", headers=world.h("bob")).json()["items"]] == [a["id"]]
    assert client.get("/items?q=refu%20%26%20'%3B--", headers=world.h("bob")).status_code == 200  # no query injection


def test_attention_views_and_dashboard_counts(client, world, mk_item):
    unowned = mk_item(as_="bob", title="nobody has this")
    mine = mk_item(as_="bob", title="bob has this")
    client.post(f"/items/{mine['id']}/claim", headers=world.h("bob"))
    waiting = mk_item(as_="bob", title="needs approval")
    client.post(f"/items/{waiting['id']}/claim", headers=world.h("bob"))
    w = client.get(f"/items/{waiting['id']}", headers=world.h("bob")).json()
    client.post(f"/items/{waiting['id']}/transition",
                json={"expected_version": w["version"], "to": "pending_approval"}, headers=world.h("bob"))

    def ids(view, who):
        return {i["id"] for i in client.get(f"/items?view={view}", headers=world.h(who)).json()["items"]}

    assert ids("unassigned", "carol") == {unowned["id"]}
    assert ids("mine", "bob") == {mine["id"], waiting["id"]}
    assert ids("needs_approval", "alice") == {waiting["id"]}  # alice manages support
    assert ids("needs_approval", "bob") == set()  # a plain member is not an approver

    counts = client.get("/dashboard", headers=world.h("alice")).json()["counts"]
    assert counts["needs_approval"] == 1 and counts["unassigned"] == 1


def test_concurrent_activity_on_one_item_keeps_history_consistent(client, world, mk_item):
    item = mk_item()
    run_concurrently(10, lambda i: client.post(
        f"/items/{item['id']}/comments", json={"body": f"c{i}"}, headers=world.h("bob" if i % 2 else "carol")))
    events = client.get(f"/items/{item['id']}/events?limit=50", headers=world.h("bob")).json()["events"]
    assert [e["kind"] for e in events].count("comment") == 10


def test_login(client, world):
    from app.db import SessionLocal
    from app.models import User
    from app.security import hash_password

    with SessionLocal() as db:
        db.get(User, world.u["bob"]).password_hash = hash_password("correct horse")
        db.commit()
    ok = client.post("/auth/login", json={"email": "Bob@T.io", "password": "correct horse"})
    assert ok.status_code == 200 and ok.json()["user"]["memberships"][0]["role"] == "member"
    assert client.get("/me", headers={"Authorization": f"Bearer {ok.json()['access_token']}"}).status_code == 200
    bad = client.post("/auth/login", json={"email": "bob@t.io", "password": "wrong"})
    assert bad.status_code == 401
    assert client.post("/auth/login", json={"email": "nobody@t.io", "password": "x"}).status_code == 401
