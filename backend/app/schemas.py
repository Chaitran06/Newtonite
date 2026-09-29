from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PriorityName = Literal["low", "medium", "high", "urgent"]
StatusName = Literal["open", "in_progress", "pending_approval", "resolved", "closed"]


class UserRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=72)


class Membership(BaseModel):
    team_id: int
    team_name: str
    role: str


class MeOut(BaseModel):
    id: int
    name: str
    email: str
    memberships: list[Membership]


class LoginOut(BaseModel):
    access_token: str
    user: MeOut


class ItemCreate(BaseModel):
    team_id: int
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=20000)
    priority: PriorityName = "medium"
    due_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v


class ItemPatch(BaseModel):
    """Partial update. Only fields that are present are changed (explicit null clears due_at)."""

    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=20000)
    priority: PriorityName | None = None
    due_at: datetime | None = None


class AssignIn(BaseModel):
    expected_version: int = Field(ge=1)
    user_id: int | None = None  # null = unassign


class TransitionIn(BaseModel):
    expected_version: int = Field(ge=1)
    to: StatusName
    note: str | None = Field(default=None, max_length=2000)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("body")
    @classmethod
    def _strip_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("comment must not be blank")
        return v


class ItemSummary(BaseModel):
    id: int
    team_id: int
    team_name: str
    title: str
    status: StatusName
    priority: PriorityName
    owner: UserRef | None
    created_by: UserRef
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class Permissions(BaseModel):
    role: str
    can_edit: bool
    can_claim: bool
    can_release: bool
    can_assign: bool
    can_comment: bool
    transitions: list[StatusName]  # transitions this user may perform right now


class ItemDetail(ItemSummary):
    description: str
    permissions: Permissions


class ItemPage(BaseModel):
    items: list[ItemSummary]
    next_cursor: str | None


class EventOut(BaseModel):
    id: int
    item_id: int
    kind: str
    actor: UserRef
    data: dict[str, Any]
    created_at: datetime


class EventPage(BaseModel):
    events: list[EventOut]
    next_cursor: int | None


class MemberOut(BaseModel):
    id: int
    name: str
    role: str


class DashboardOut(BaseModel):
    counts: dict[str, int]
    by_status: dict[str, int]
    teams: list[Membership]
