from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.db import get_db
from app.errors import ApiError
from app.models import Membership, Team, User
from app.security import create_token, get_current_user, hash_password, verify_password

router = APIRouter(tags=["auth"])

# Verified against a real (but useless) hash when the email is unknown, so response time
# does not reveal whether an account exists.
_DUMMY_HASH = hash_password("not-a-real-password")


def _me(db: Session, user: User) -> schemas.MeOut:
    rows = db.execute(
        select(Membership.team_id, Team.name, Membership.role)
        .join(Team, Team.id == Membership.team_id)
        .where(Membership.user_id == user.id)
        .order_by(Team.name)
    ).all()
    return schemas.MeOut(
        id=user.id,
        name=user.name,
        email=user.email,
        memberships=[schemas.Membership(team_id=t, team_name=n, role=r) for t, n, r in rows],
    )


@router.post("/auth/login", response_model=schemas.LoginOut)
def login(payload: schemas.LoginIn, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == payload.email.strip().lower())).scalar_one_or_none()
    ok = verify_password(payload.password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not ok:
        raise ApiError(401, "invalid_credentials", "Incorrect email or password")
    return schemas.LoginOut(access_token=create_token(user.id), user=_me(db, user))


@router.get("/me", response_model=schemas.MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _me(db, user)
