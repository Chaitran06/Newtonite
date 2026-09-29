"""Tests run against a REAL Postgres (never SQLite): the behaviours we care about -
row-level atomicity, unique-index blocking, READ COMMITTED re-checks - only exist there.

    createdb opsdesk_test
    TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/opsdesk_test pytest
"""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/opsdesk_test"
)
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-1234567890"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Membership, Team, User  # noqa: E402
from app.security import create_token  # noqa: E402

assert engine.url.database.endswith("_test"), "refusing to run tests against a non-test database"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def _clean():
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE item_events, idempotency_keys, work_items, memberships, teams, users "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


class World:
    """support team: alice=manager, bob=member, carol=member, dave=viewer
    eng team:     erin=manager
    (erin is NOT in support; alice/bob/carol/dave are NOT in eng)"""

    def __init__(self):
        self.u: dict[str, int] = {}
        self.team: dict[str, int] = {}

    def h(self, name: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_token(self.u[name])}"}


@pytest.fixture()
def world() -> World:
    w = World()
    with SessionLocal() as db:
        users = {n: User(email=f"{n}@t.io", name=n.title(), password_hash="x") for n in
                 ("alice", "bob", "carol", "dave", "erin")}
        teams = {"support": Team(name="Support"), "eng": Team(name="Engineering")}
        db.add_all([*users.values(), *teams.values()])
        db.flush()
        for n, t, r in [("alice", "support", "manager"), ("bob", "support", "member"),
                        ("carol", "support", "member"), ("dave", "support", "viewer"),
                        ("erin", "eng", "manager")]:
            db.add(Membership(user_id=users[n].id, team_id=teams[t].id, role=r))
        db.commit()
        w.u = {n: u.id for n, u in users.items()}
        w.team = {n: t.id for n, t in teams.items()}
    return w


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def mk_item(client, world):
    def _mk(as_: str = "bob", team: str = "support", **fields):
        body = {"team_id": world.team[team], "title": "Payment stuck in pending", **fields}
        r = client.post("/items", json=body, headers=world.h(as_))
        assert r.status_code == 201, r.text
        return r.json()

    return _mk


def run_concurrently(n: int, fn):
    """Run fn(i) in n threads that all start at the same instant."""
    barrier = Barrier(n)

    def _wrapped(i):
        barrier.wait()
        return fn(i)

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(_wrapped, range(n)))
