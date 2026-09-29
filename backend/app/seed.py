"""Create tables and load demo data.

    python -m app.seed                  # ~400 demo items
    python -m app.seed --items 20000    # scale demo
    python -m app.seed --reset          # drop everything first

All demo users share the password: password123
"""

import argparse
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import insert

from app.db import SessionLocal, engine
from app.models import Base, ItemEvent, Membership, Team, User, WorkItem
from app.security import hash_password
from app.workflow import PRIORITY_NAMES

PASSWORD = "password123"

USERS = [
    ("alice@example.com", "Alice Anders"),
    ("bob@example.com", "Bob Brandt"),
    ("carol@example.com", "Carol Chen"),
    ("dave@example.com", "Dave Diaz"),
    ("erin@example.com", "Erin Evans"),
    ("frank@example.com", "Frank Fischer"),
    ("grace@example.com", "Grace Gupta"),
]
TEAMS = ["Support", "Engineering", "Payments"]
# (user email, team, role)
MEMBERSHIPS = [
    ("alice@example.com", "Support", "manager"),
    ("alice@example.com", "Payments", "member"),
    ("bob@example.com", "Support", "member"),
    ("carol@example.com", "Support", "member"),
    ("carol@example.com", "Engineering", "manager"),
    ("dave@example.com", "Support", "viewer"),
    ("erin@example.com", "Engineering", "manager"),
    ("frank@example.com", "Engineering", "member"),
    ("grace@example.com", "Payments", "manager"),
]

SUBJECTS = {
    "Support": ["Customer cannot log in", "Refund request not processed", "Order stuck in checkout", "Wrong invoice sent",
                "Account locked after password reset", "Delivery marked delivered but not received", "Duplicate charge reported"],
    "Engineering": ["Production API latency spike", "Deploy pipeline failing on main", "Memory leak in worker service",
                    "Rotate expiring TLS certificate", "Database replica lagging", "Feature flag stuck on in staging"],
    "Payments": ["Payment stuck in pending", "Chargeback investigation", "Settlement mismatch with bank", "Suspicious transaction review",
                 "Failed payout to vendor", "Currency conversion rounding difference"],
}
DETAILS = ["Reported by multiple users this morning.", "Needs approval before we can proceed.", "Blocked on a reply from the vendor.",
           "Affects a large customer account.", "Compliance has asked for an update.", "Low impact but keeps recurring."]


def seed(item_count: int, reset: bool) -> None:
    if reset:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    rng = random.Random(42)

    with SessionLocal() as db:
        if db.query(User).count() > 0:
            print("Database already has data; use --reset to start over.")
            return

        pw = hash_password(PASSWORD)
        users = {e: User(email=e, name=n, password_hash=pw) for e, n in USERS}
        teams = {n: Team(name=n) for n in TEAMS}
        db.add_all([*users.values(), *teams.values()])
        db.flush()
        db.add_all(Membership(user_id=users[e].id, team_id=teams[t].id, role=r) for e, t, r in MEMBERSHIPS)
        db.flush()

        members_by_team: dict[str, list[User]] = {t: [] for t in TEAMS}
        managers_by_team: dict[str, list[User]] = {t: [] for t in TEAMS}
        for e, t, r in MEMBERSHIPS:
            if r in ("member", "manager"):
                members_by_team[t].append(users[e])
            if r == "manager":
                managers_by_team[t].append(users[e])

        now = datetime.now(timezone.utc)
        rows = []
        for i in range(item_count):
            team = rng.choice(TEAMS)
            creator = rng.choice(members_by_team[team])
            created = now - timedelta(minutes=rng.randint(10, 60 * 24 * 60))
            updated = created + timedelta(minutes=rng.randint(0, int((now - created).total_seconds() // 60)))
            status = rng.choices(
                ["open", "in_progress", "pending_approval", "resolved", "closed"], [30, 30, 8, 17, 15]
            )[0]
            owner = None
            if status in ("in_progress", "pending_approval") or (status != "open" and rng.random() < 0.9) or rng.random() < 0.2:
                owner = rng.choice(members_by_team[team])
            due = None
            if rng.random() < 0.4:
                due = now + timedelta(hours=rng.randint(-72, 24 * 14))
            rows.append(
                dict(
                    team_id=teams[team].id,
                    title=f"{rng.choice(SUBJECTS[team])} #{i + 1}",
                    description=f"{rng.choice(DETAILS)} {rng.choice(DETAILS)}",
                    status=status,
                    priority=rng.choices([1, 2, 3, 4], [20, 45, 25, 10])[0],
                    owner_id=owner.id if owner else None,
                    created_by=creator.id,
                    due_at=due,
                    created_at=created,
                    updated_at=updated,
                )
            )

        ids: list[int] = []
        for start in range(0, len(rows), 2000):
            chunk = rows[start : start + 2000]
            ids += list(db.execute(insert(WorkItem).returning(WorkItem.id), chunk).scalars())

        names = {u.id: u.name for u in users.values()}
        events = []
        for item_id, row in zip(ids, rows):
            events.append(dict(item_id=item_id, actor_id=row["created_by"], kind="created",
                               data={"title": row["title"], "priority": PRIORITY_NAMES[row["priority"]]}, created_at=row["created_at"]))
            if row["owner_id"]:
                events.append(dict(item_id=item_id, actor_id=row["owner_id"], kind="assigned",
                                   data={"from": None, "to": {"id": row["owner_id"], "name": names[row["owner_id"]]}, "via": "claim"},
                                   created_at=row["created_at"] + timedelta(minutes=5)))
        for start in range(0, len(events), 5000):
            db.execute(insert(ItemEvent), events[start : start + 5000])
        db.commit()
        print(f"Seeded {len(USERS)} users, {len(TEAMS)} teams, {len(rows)} items, {len(events)} events.")
        print(f"Log in with any of: {', '.join(e for e, _ in USERS)}  /  password: {PASSWORD}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=int, default=400)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    seed(args.items, args.reset)
