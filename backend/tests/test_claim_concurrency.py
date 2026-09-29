"""Critical behaviour #1: two people try to take responsibility for the same work at once."""

from tests.conftest import run_concurrently


def test_only_one_of_many_concurrent_claims_wins(client, world, mk_item):
    item = mk_item(as_="alice")
    # alice, bob and carol race (each several times, as different people would from different tabs)
    claimers = ["alice", "bob", "carol"] * 4

    results = run_concurrently(
        len(claimers),
        lambda i: client.post(f"/items/{item['id']}/claim", headers=world.h(claimers[i])),
    )

    winners = {claimers[i] for i, r in enumerate(results) if r.status_code == 200}
    assert len(winners) == 1, f"expected exactly one winning claimer, got {winners}"
    winner = winners.pop()
    for i, r in enumerate(results):
        if claimers[i] != winner:
            assert r.status_code == 409 and r.json()["error"]["code"] == "already_claimed"

    final = client.get(f"/items/{item['id']}", headers=world.h("alice")).json()
    assert final["owner"]["id"] == world.u[winner] and final["status"] == "in_progress"

    # History records the claim exactly once - no duplicate "assigned" events from the losers.
    events = client.get(f"/items/{item['id']}/events", headers=world.h("alice")).json()["events"]
    assert [e["kind"] for e in events].count("assigned") == 1


def test_claiming_something_you_already_own_is_a_harmless_repeat(client, world, mk_item):
    item = mk_item()
    assert client.post(f"/items/{item['id']}/claim", headers=world.h("bob")).status_code == 200
    again = client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))
    assert again.status_code == 200  # not an error: the user's intent is already satisfied
    events = client.get(f"/items/{item['id']}/events", headers=world.h("bob")).json()["events"]
    assert [e["kind"] for e in events].count("assigned") == 1


def test_claim_loser_gets_a_clear_reason(client, world, mk_item):
    item = mk_item()
    client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))
    r = client.post(f"/items/{item['id']}/claim", headers=world.h("carol"))
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "already_claimed" and err["owner"]["name"] == "Bob"


def test_release_then_claim_by_someone_else(client, world, mk_item):
    item = mk_item()
    client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))
    rel = client.post(f"/items/{item['id']}/release", headers=world.h("bob"))
    assert rel.status_code == 200 and rel.json()["owner"] is None and rel.json()["status"] == "open"
    assert client.post(f"/items/{item['id']}/claim", headers=world.h("carol")).status_code == 200


def test_only_the_owner_can_release(client, world, mk_item):
    item = mk_item()
    client.post(f"/items/{item['id']}/claim", headers=world.h("bob"))
    r = client.post(f"/items/{item['id']}/release", headers=world.h("carol"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_owner"
