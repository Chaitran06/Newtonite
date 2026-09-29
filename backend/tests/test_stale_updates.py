"""Critical behaviour #2: stale information. Someone edits an item from an out-of-date copy."""

from tests.conftest import run_concurrently


def test_stale_edit_is_rejected_and_does_not_overwrite(client, world, mk_item):
    item = mk_item(as_="bob", title="Original")
    v1 = item["version"]

    # Alice (manager) edits first.
    ok = client.patch(f"/items/{item['id']}", json={"expected_version": v1, "priority": "urgent"},
                      headers=world.h("alice"))
    assert ok.status_code == 200 and ok.json()["version"] == v1 + 1

    # Bob still looks at version v1 and tries to change the title.
    stale = client.patch(f"/items/{item['id']}", json={"expected_version": v1, "title": "Bob's title"},
                         headers=world.h("bob"))
    assert stale.status_code == 409
    err = stale.json()["error"]
    assert err["code"] == "stale_version" and err["current_version"] == v1 + 1

    current = client.get(f"/items/{item['id']}", headers=world.h("bob")).json()
    assert current["title"] == "Original" and current["priority"] == "urgent"  # nothing was lost


def test_concurrent_edits_from_the_same_version_only_one_wins(client, world, mk_item):
    item = mk_item(as_="bob")
    who = ["alice", "bob"] * 4
    results = run_concurrently(
        len(who),
        lambda i: client.patch(
            f"/items/{item['id']}",
            json={"expected_version": item["version"], "title": f"edit by {who[i]} #{i}"},
            headers=world.h(who[i]),
        ),
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and codes.count(409) == len(who) - 1

    events = client.get(f"/items/{item['id']}/events", headers=world.h("alice")).json()["events"]
    assert [e["kind"] for e in events].count("updated") == 1  # one history entry, matching the one write


def test_comments_do_not_invalidate_other_peoples_edits(client, world, mk_item):
    item = mk_item(as_="bob")
    assert client.post(f"/items/{item['id']}/comments", json={"body": "looking into it"},
                       headers=world.h("carol")).status_code == 201
    r = client.patch(f"/items/{item['id']}", json={"expected_version": item["version"], "priority": "high"},
                     headers=world.h("bob"))
    assert r.status_code == 200


def test_noop_edit_does_not_bump_version_or_write_history(client, world, mk_item):
    item = mk_item(as_="bob", priority="high")
    r = client.patch(f"/items/{item['id']}", json={"expected_version": item["version"], "priority": "high"},
                     headers=world.h("bob"))
    assert r.status_code == 200 and r.json()["version"] == item["version"]
    events = client.get(f"/items/{item['id']}/events", headers=world.h("bob")).json()["events"]
    assert [e["kind"] for e in events] == ["created"]


def test_transition_on_a_stale_view_is_rejected(client, world, mk_item):
    item = mk_item(as_="bob")
    client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))  # version moves on
    r = client.post(f"/items/{item['id']}/transition",
                    json={"expected_version": item["version"], "to": "closed"}, headers=world.h("bob"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "stale_version"
