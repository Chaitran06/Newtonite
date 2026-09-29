# Engineering Decisions

Five decisions that shaped this system, what I gave up for each, and what I chose not to build.
A smaller system with behaviour I can defend beat a larger one with more half-working features.

## Assumptions

- Teams are the unit of access: you see and act on an item because you belong to *its team*.
  A user can hold different roles in different teams.
- "Operational work" is short-lived and collaborative: dozens of people may touch the same item within
  minutes, but a single item rarely has more than a few concurrent editors.
- Tens of thousands of active items and a large history table matter more than raw write throughput.
- Losing an edit silently is worse than asking a user to retry. When unsure, refuse loudly.

---

## 1. PostgreSQL is the correctness engine, not just storage

**Decision.** One PostgreSQL database holds items, history, memberships and idempotency records. I lean on
what it guarantees natively instead of re-implementing it in application code: atomic single-statement
updates, unique indexes that block concurrent inserts, `CHECK` constraints, a generated `tsvector` column
with a GIN index for search, and transactions that make "change + history + idempotency record" all-or-nothing.

**Why.** Almost every hard requirement in the brief (two people claiming, duplicates, consistency) is a
statement about *atomicity at the row level*. Doing that in app memory or a cache would need locks and
would break the moment there is a second app instance. Postgres already does it, correctly, per row.

**Trade-offs.**
- Postgres is a hard dependency for development *and tests* (SQLite would silently hide the races).
- One database for OLTP, search and audit is simple but couples their scaling. At much larger scale I would
  move search to a dedicated engine and archive old history. Not needed at the stated scale, and every
  extra system is a new failure mode.
- Invariants are duplicated on purpose: "in-progress work always has an owner" is a workflow rule in code
  *and* a `CHECK` constraint. The constraint costs nothing and catches the bug I did not think of.

## 2. Three different concurrency mechanisms for three different problems

**Decision.** I did not use one mechanism everywhere.

| Operation | Mechanism | Reason |
|-----------|-----------|--------|
| **Claim** | one conditional `UPDATE ... WHERE owner_id IS NULL AND status='open'` | The winner is decided by the database in one statement. There is no read-then-write gap to race in. |
| **Edit / assign / status change** | optimistic concurrency: `UPDATE ... WHERE version = :expected`, `409` if zero rows | The user made a decision *based on what they saw*. If it changed, they must look again. No lock is held while they think. |
| **Release** | short `SELECT ... FOR UPDATE` | Its side effects (reset status to `open`) depend on the *current* status, so it needs a consistent read of the row for a moment. |

**Why not just one?** A version check on claim would tell the loser "stale" when the truthful answer is
"Bob already has it", and would make two people claiming an untouched item fail spuriously. Pessimistic
locks for edits would hold a row across user think-time. Each mechanism is the cheapest one that is still correct.

**Details that matter.**
- Comments do **not** bump `version`; otherwise a comment would invalidate everyone's in-progress edit.
  They only bump `updated_at` (the "last activity" used for ordering).
- A no-op edit changes nothing: no version bump, no history entry.
- Claiming something already yours is a harmless `200`, not an error: the user's intent is satisfied.
- Every `409` says *why* (`already_claimed` with the owner, `stale_version` with the current version),
  so the UI can explain rather than show a generic failure.

**Trade-offs.** Optimistic concurrency pushes a retry onto the user. For operational work that is the
right call, and the edit form offers to re-apply their changes on top of the latest version (only the fields
they actually changed; untouched fields take the server's value, so nobody's change is silently reverted).

## 3. Idempotency keys committed in the same transaction as the change

**Decision.** Mutating endpoints accept an `Idempotency-Key`. The key row is inserted in the *same
transaction* as the business change and the stored response; it becomes visible only when they all commit.

**Why this shape.**
- A concurrent duplicate blocks on the unique index until the first request finishes, then replays its
  stored result. Two simultaneous double-clicks execute once, with no application-level lock.
- If the first request fails or rolls back, its key never existed, so the retry is a fresh first attempt.
  Only `2xx` outcomes are stored.
- The key is bound to a hash of method + path + body. Reusing a key for a *different* request is a `422`,
  not a silent return of an unrelated response.
- The client generates one key per user *intention* (per open form, per composed comment) and reuses it on
  retry. The API client retries network failures automatically **only** when it holds a key, because that
  is the only case where a retry is provably safe.

**Trade-offs.** Extra write per mutation and a table that needs a retention job (not built). Keys are per
user. Idempotency handles "did my request go through?"; the version check handles "did someone else change it?".
They solve different problems and I kept them separate.

## 4. Authorization is per-resource, enforced in the service layer and inside queries

**Decision.**
- Roles live on the membership (`viewer` < `member` < `manager`), per team.
- Every operation loads the item and checks the caller's role **in that item's team**. Not a member: `404`
  (existence is not leaked). Member with too low a role: `403`.
- Lists are scoped *inside the SQL* (`team_id IN (my teams)`); there is no fetch-then-filter step to forget.
- Workflow rules are pure functions (`check_transition`). The API uses them to enforce, and the same
  function computes the `permissions.transitions` list returned to the UI. The UI cannot offer an action the
  server would refuse, because both come from one function. The UI still gets no authority: every request is re-checked.
- Approval gate: only managers can mark an item resolved or act on one pending approval.

**Trade-offs.** Roles are read from the database on every request rather than embedded in the token: one
cheap indexed lookup, in exchange for role changes taking effect immediately and no stale privileges in a
JWT. There is no per-item ACL; team-level roles are coarse but easy to reason about and explain.

## 5. History is written with the change, and lists use keyset pagination

**Decision (history).** Every change writes an attributed row to an append-only `item_events` table inside
the same transaction (field-level before/after for edits, who/why for status changes, comments in the same
timeline). There is no API to edit or delete an event.

**Why not an async audit pipeline?** A change that committed but whose audit record was lost is precisely
the "silently disappearing action" the brief warns about. Writing both in one transaction makes that impossible.

**Decision (lists).** Cursor (keyset) pagination on `(priority, updated_at, id)` with matching indexes,
filters and search executed in SQL, nothing loaded wholesale into the browser.

**Why not `OFFSET`?** Cost grows with depth and rows repeat or vanish as new work arrives. Measured locally with
30,000 seeded items: the default "all my teams" query was a sequential scan plus sort (~11 ms, growing with table size)
until I added sort-order indexes, after which it is an index scan of 26 rows (~0.3 ms), and page 300 costs the same as page 1.

**Trade-offs.**
- Sort orders are fixed (needs-attention, recent activity). Arbitrary sorting would need more indexes.
- Search is English full-text with prefix matching, with no relevance ranking, so pagination stays stable.
- Event ids are assigned at insert, not commit, so under heavy concurrency a page could briefly show two
  events slightly out of commit order. Harmless for a timeline.

---

## What I intentionally did not build

- **Asynchronous processing / notifications.** The brief allows but does not require it, and none of the
  correctness behaviours I chose need it. I would rather leave it out than ship an untested worker. The
  design I would build next is a **transactional outbox**: write a `notifications_outbox` row in the same
  transaction as the event; a worker claims rows with `SELECT ... FOR UPDATE SKIP LOCKED`; delivery is made
  idempotent by a unique `(event_id, recipient)` so at-least-once execution never double-notifies; failed rows
  retry with backoff and a max-attempts dead-letter state. That answers "fails, runs twice, or is delayed".
- **Real-time push.** Polling plus refetch-on-focus is enough because the version check and claim guard make
  stale screens *safe*, only briefly out of date. SSE/WebSockets would improve freshness, not correctness.
- **Admin UI for users/teams/roles, migrations, rate limiting, refresh tokens.** Each is standard but none
  bears on the core reliability story. Listed under known limitations in the README.

## If this grew 10x

- Cache membership lookups per request/short TTL; add read replicas for list/search queries.
- Replace live dashboard `COUNT`s with maintained counters or a materialized view.
- Partition `item_events` by time and archive cold history; move search to a dedicated engine.
- Add the outbox worker above, then push updates over SSE, fed from the same events.
- Move from `create_all` to migrations and add connection pooling in front of Postgres (PgBouncer).

## With another week

1. Outbox worker, notifications and their failure-mode tests.
2. Browser end-to-end tests for the two-user claim race and the edit-conflict flow.
3. Membership/role administration with its own audit trail.
4. SSE for live updates; an "ownership changed while you were viewing" indicator.
5. Idempotency-key retention job, login rate limiting, HttpOnly-cookie sessions instead of `localStorage`.
