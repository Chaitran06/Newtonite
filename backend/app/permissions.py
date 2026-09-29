"""Role model. Roles are per team (a user can be manager in one team, viewer in another).

viewer  - read items and history of the team
member  - viewer + create items, comment, claim unowned work; may modify items they own or created
manager - member + modify any item, assign/unassign anyone, approve (resolve), reopen closed items

Every check is made server-side against the *item's* team, not against a global flag.
A user who is not in the item's team gets 404 (existence is not leaked), a user with too
low a role gets 403.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import Membership

RANK = {"viewer": 0, "member": 1, "manager": 2}


def get_role(db: Session, user_id: int, team_id: int) -> str | None:
    return db.execute(
        select(Membership.role).where(Membership.user_id == user_id, Membership.team_id == team_id)
    ).scalar_one_or_none()


def my_roles(db: Session, user_id: int) -> dict[int, str]:
    rows = db.execute(select(Membership.team_id, Membership.role).where(Membership.user_id == user_id)).all()
    return {team_id: role for team_id, role in rows}


def at_least(role: str | None, minimum: str) -> bool:
    return role is not None and RANK[role] >= RANK[minimum]


def require_role(role: str | None, minimum: str, action: str) -> None:
    if role is None:
        raise ApiError(404, "not_found", "Not found")
    if not at_least(role, minimum):
        raise ApiError(403, "forbidden", f"Your role ({role}) does not allow you to {action}")


def can_modify(role: str | None, owner_id: int | None, created_by: int, user_id: int) -> bool:
    """Managers can modify anything in their team; members only what they own or created."""
    if role == "manager":
        return True
    return at_least(role, "member") and user_id in (owner_id, created_by)
