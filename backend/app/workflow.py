"""Workflow rules. Pure functions, no DB access, so they are trivially unit-testable and the
same function drives both enforcement (API) and the "what can I do next" hints (UI)."""

from app.errors import ApiError
from app.permissions import can_modify

OPEN = "open"
IN_PROGRESS = "in_progress"
PENDING_APPROVAL = "pending_approval"
RESOLVED = "resolved"
CLOSED = "closed"

STATUSES = (OPEN, IN_PROGRESS, PENDING_APPROVAL, RESOLVED, CLOSED)
ACTIVE = (OPEN, IN_PROGRESS, PENDING_APPROVAL)
DONE = (RESOLVED, CLOSED)

TRANSITIONS: dict[str, tuple[str, ...]] = {
    OPEN: (IN_PROGRESS, CLOSED),
    IN_PROGRESS: (OPEN, PENDING_APPROVAL, RESOLVED),
    PENDING_APPROVAL: (IN_PROGRESS, RESOLVED),  # reject -> in_progress, approve -> resolved
    RESOLVED: (CLOSED, OPEN),
    CLOSED: (OPEN,),  # reopen
}

PRIORITIES = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
PRIORITY_NAMES = {v: k for k, v in PRIORITIES.items()}


def check_transition(
    *,
    status: str,
    owner_id: int | None,
    created_by: int,
    role: str | None,
    user_id: int,
    to: str,
) -> ApiError | None:
    """Return the reason a transition is not allowed, or None if it is allowed."""
    if to not in TRANSITIONS.get(status, ()):
        return ApiError(409, "illegal_transition", f"An item that is {status} cannot move to {to}")

    is_manager = role == "manager"

    # Approval gate: only managers resolve items or act on items awaiting approval.
    if to == RESOLVED and not is_manager:
        return ApiError(
            403, "approval_required", "Only a team manager can mark an item resolved; request approval instead"
        )
    if status == PENDING_APPROVAL and not is_manager:
        return ApiError(403, "approval_required", "Only a team manager can act on an item pending approval")
    if status == CLOSED and not is_manager:
        return ApiError(403, "forbidden", "Only a team manager can reopen a closed item")

    # Work being done must have somebody responsible for it.
    if to in (IN_PROGRESS, PENDING_APPROVAL) and owner_id is None:
        return ApiError(409, "owner_required", "Claim or assign an owner before starting work on this item")

    if not can_modify(role, owner_id, created_by, user_id):
        return ApiError(403, "forbidden", "Only the owner, the creator or a team manager can change this item")
    return None
