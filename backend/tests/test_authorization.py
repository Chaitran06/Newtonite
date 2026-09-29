"""Critical behaviour #4: authorization enforced by the server, per resource (per team)."""


def test_requests_without_a_token_are_rejected(client):
    assert client.get("/items").status_code == 401
    assert client.post("/items", json={}).status_code == 401


def test_non_member_cannot_see_or_touch_another_teams_item(client, world, mk_item):
    item = mk_item(as_="bob")  # support team
    erin = world.h("erin")  # engineering manager, not in support
    assert client.get(f"/items/{item['id']}", headers=erin).status_code == 404  # existence not leaked
    assert client.get(f"/items/{item['id']}/events", headers=erin).status_code == 404
    assert client.post(f"/items/{item['id']}/claim", headers=erin).status_code == 404
    assert client.post(f"/items/{item['id']}/comments", json={"body": "x"}, headers=erin).status_code == 404
    assert client.patch(f"/items/{item['id']}", json={"expected_version": 1, "title": "x"},
                        headers=erin).status_code == 404


def test_listing_only_returns_items_from_my_teams(client, world, mk_item):
    support_item = mk_item(as_="bob", title="Support thing")
    eng_item = mk_item(as_="erin", team="eng", title="Engineering thing")
    bob_ids = {i["id"] for i in client.get("/items", headers=world.h("bob")).json()["items"]}
    erin_ids = {i["id"] for i in client.get("/items", headers=world.h("erin")).json()["items"]}
    assert bob_ids == {support_item["id"]} and erin_ids == {eng_item["id"]}
    # Asking for a foreign team explicitly is treated like it does not exist.
    assert client.get(f"/items?team_id={world.team['eng']}", headers=world.h("bob")).status_code == 404


def test_role_is_per_team_not_global(client, world):
    # dave is a viewer in support: can read, cannot write.
    dave = world.h("dave")
    r = client.post("/items", json={"team_id": world.team["support"], "title": "x"}, headers=dave)
    assert r.status_code == 403
    # ...and creating in a team you are not in looks like the team does not exist
    r = client.post("/items", json={"team_id": world.team["eng"], "title": "x"}, headers=dave)
    assert r.status_code == 404


def test_viewer_can_read_but_not_comment_or_claim(client, world, mk_item):
    item = mk_item(as_="bob")
    dave = world.h("dave")
    assert client.get(f"/items/{item['id']}", headers=dave).status_code == 200
    assert client.get(f"/items/{item['id']}/events", headers=dave).status_code == 200
    assert client.post(f"/items/{item['id']}/comments", json={"body": "hi"}, headers=dave).status_code == 403
    assert client.post(f"/items/{item['id']}/claim", headers=dave).status_code == 403


def test_member_cannot_edit_someone_elses_item_but_manager_can(client, world, mk_item):
    item = mk_item(as_="bob")
    body = {"expected_version": item["version"], "priority": "urgent"}
    assert client.patch(f"/items/{item['id']}", json=body, headers=world.h("carol")).status_code == 403
    assert client.patch(f"/items/{item['id']}", json=body, headers=world.h("alice")).status_code == 200


def test_only_managers_can_assign_and_assignee_must_be_a_member(client, world, mk_item):
    item = mk_item(as_="bob")
    v = item["version"]
    body = {"expected_version": v, "user_id": world.u["carol"]}
    assert client.post(f"/items/{item['id']}/assign", json=body, headers=world.h("bob")).status_code == 403

    bad = client.post(f"/items/{item['id']}/assign", json={"expected_version": v, "user_id": world.u["dave"]},
                      headers=world.h("alice"))  # viewer
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_assignee"
    outsider = client.post(f"/items/{item['id']}/assign", json={"expected_version": v, "user_id": world.u["erin"]},
                           headers=world.h("alice"))  # not in the team
    assert outsider.status_code == 422

    ok = client.post(f"/items/{item['id']}/assign", json=body, headers=world.h("alice"))
    assert ok.status_code == 200 and ok.json()["owner"]["id"] == world.u["carol"]


def test_permissions_in_the_response_match_what_the_server_enforces(client, world, mk_item):
    item = mk_item(as_="bob")
    as_dave = client.get(f"/items/{item['id']}", headers=world.h("dave")).json()["permissions"]
    assert not any([as_dave["can_edit"], as_dave["can_claim"], as_dave["can_comment"], as_dave["can_assign"]])
    assert as_dave["transitions"] == []
    as_alice = client.get(f"/items/{item['id']}", headers=world.h("alice")).json()["permissions"]
    assert as_alice["can_assign"] and as_alice["can_claim"]
