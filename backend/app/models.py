from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class Membership(Base):
    """A user can belong to many teams with a different role in each."""

    __tablename__ = "memberships"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))

    __table_args__ = (
        CheckConstraint("role in ('viewer','member','manager')", name="ck_membership_role"),
        Index("ix_memberships_team", "team_id"),
    )


class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    priority: Mapped[int] = mapped_column(Integer, default=2)  # 1 low .. 4 urgent
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Bumped on every change to the item's own fields. Used for optimistic concurrency.
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # "Last activity": bumped by edits AND comments. Drives ordering, not concurrency.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Generated column: full-text search vector maintained by Postgres itself.
    search: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))",
            persisted=True,
        ),
        deferred=True,
    )

    team: Mapped[Team] = relationship(lazy="joined", innerjoin=True)
    owner: Mapped[User | None] = relationship(foreign_keys=[owner_id], lazy="joined")
    creator: Mapped[User] = relationship(foreign_keys=[created_by], lazy="joined", innerjoin=True)

    __table_args__ = (
        CheckConstraint(
            "status in ('open','in_progress','pending_approval','resolved','closed')",
            name="ck_item_status",
        ),
        CheckConstraint("priority between 1 and 4", name="ck_item_priority"),
        # Invariant enforced by the DB itself, not just application code:
        # work that is being done (or awaiting approval) always has an owner.
        CheckConstraint(
            "status not in ('in_progress','pending_approval') or owner_id is not null",
            name="ck_item_active_has_owner",
        ),
        # Keyset-pagination sort orders. Postgres scans these backwards for ORDER BY ... DESC.
        # Per-team indexes serve single-team views; the team-less ones serve the common
        # "all my teams" list (team_id IN (...) is applied as a filter while walking the index,
        # which stops after LIMIT rows instead of sorting every visible row).
        Index("ix_items_team_updated", "team_id", "updated_at", "id"),
        Index("ix_items_team_prio", "team_id", "priority", "updated_at", "id"),
        Index("ix_items_attention", "priority", "updated_at", "id"),
        Index("ix_items_recent", "updated_at", "id"),
        Index("ix_items_owner_status", "owner_id", "status"),
        Index("ix_items_status_due", "status", "due_at"),
        Index("ix_items_search", "search", postgresql_using="gin"),
    )


class ItemEvent(Base):
    """Append-only history + comments for a work item. No update or delete code path exists."""

    __tablename__ = "item_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))  # created|updated|status_changed|assigned|comment
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    actor: Mapped[User] = relationship(lazy="joined", innerjoin=True)

    __table_args__ = (Index("ix_events_item", "item_id", "id"),)


class IdempotencyKey(Base):
    """Remembers the outcome of a successful mutating request so a retry replays it."""

    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),)
