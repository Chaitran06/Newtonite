from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.db import get_db
from app.errors import ApiError
from app.models import Membership, User
from app.permissions import get_role
from app.security import get_current_user
from app.services import items as svc

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=schemas.DashboardOut)
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return svc.dashboard(db, user)


@router.get("/teams/{team_id}/members", response_model=list[schemas.MemberOut])
def team_members(team_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if get_role(db, user.id, team_id) is None:
        raise ApiError(404, "not_found", "Not found")
    rows = db.execute(
        select(User.id, User.name, Membership.role)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.team_id == team_id)
        .order_by(User.name)
    ).all()
    return [schemas.MemberOut(id=i, name=n, role=r) for i, n, r in rows]
