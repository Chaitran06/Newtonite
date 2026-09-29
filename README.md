# OpsDesk

An internal web app for coordinating operational work: customer issues, incidents, payment
investigations, approvals. One place that answers *what is this, who owns it, what happened,
and what needs attention next*, and that behaves correctly when many people use it at once.

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy 2, PostgreSQL 14+
- **Frontend:** React 18 + TypeScript (Vite), TanStack Query
- **Design rationale:** [ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md)

## Contents

1. [Run it locally](#run-it-locally)
2. [Try the interesting behaviours](#try-the-interesting-behaviours-2-minute-tour)
3. [What is built](#what-is-built)
4. [Tests](#tests)
5. [Known limitations](#known-limitations)

---

## Run it locally

No Docker required. You need **Python 3.11+**, **Node 18+** and a local **PostgreSQL 14+**.

### 1. Database

Install PostgreSQL if you do not have it (`brew install postgresql@16`, `sudo apt install postgresql`,
or the Windows installer), then create two empty databases: one to run the app, one for the tests.

```bash
createdb opsdesk
createdb opsdesk_test
```

The defaults assume user `postgres` / password `postgres` on `localhost:5432`. If yours differ,
set the connection string (see [Configuration](#configuration)).

### 2. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m app.seed                   # tables + demo users/teams + ~400 demo items
uvicorn app.main:app --reload --port 8000
```

API docs (Swagger UI): http://localhost:8000/docs

Optional: `python -m app.seed --items 30000 --reset` loads a large dataset to see pagination
and search at scale.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api/*` to the backend on port 8000.

### Demo users

All use password **`password123`**.

| User | Teams and roles |
|------|-----------------|
| alice@example.com | Support **manager**, Payments member |
| bob@example.com | Support member |
| carol@example.com | Support member, Engineering **manager** |
| dave@example.com | Support **viewer** (read-only) |
| erin@example.com | Engineering manager |
| frank@example.com | Engineering member |
| grace@example.com | Payments manager (cannot see Support at all) |

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/opsdesk` | App database |
| `TEST_DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/opsdesk_test` | Test database (tests refuse to run unless the name ends in `_test`) |
| `JWT_SECRET` | dev-only value | **Set a long random value outside local development** |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Only needed if the frontend is not served through the Vite proxy |
| `VITE_API_TARGET` | `http://localhost:8000` | Where the Vite dev proxy sends `/api` |

---

## Try the interesting behaviours (2-minute tour)

Open two browser windows (or one normal + one private window) signed in as different users.

1. **Two people claim the same work.** As `bob` and `carol`, open the *Unassigned* tile and click
   **Claim** on the same row at about the same time. Exactly one wins. The other instantly sees the
   optimistic "you own it" state roll back with *"Too late: Bob claimed this item first."*
2. **Stale edit.** As `alice` and `bob`, open the same item and click **Edit** in both. Save in one,
   then the other. The second save is refused (nothing is overwritten), a banner shows what changed,
   and the user can re-apply their edit on top of the latest version or discard it.
3. **Approval workflow.** As `bob`: claim an item, then **Request approval**. Bob cannot approve it.
   As `alice` (manager): the item appears under *Awaiting my approval*; **Approve** or **Reject**.
4. **Resource-level authorization.** `dave` (viewer) can read but has no Comment/Claim/Edit controls, and
   the API returns 403 if you call them anyway. `grace` cannot see any Support item, and
   `GET /items/<a support id>` returns 404.
5. **Repeat-safe requests.** From a terminal (get a token from the login response):

   ```bash
   curl -X POST localhost:8000/items -H "Authorization: Bearer $TOKEN" \
        -H "Idempotency-Key: demo-1" -H "content-type: application/json" \
        -d '{"team_id":1,"title":"Created once"}'
   # run the exact same command again: same item id back, `Idempotent-Replay: true`, still one item
   ```

---

## What is built

```
 React SPA ──/api──▶ FastAPI ──▶ services (use-cases) ──▶ PostgreSQL
 (TanStack Query,    routers:      one function per        work_items · item_events (append-only)
  filters in URL,    thin, auth,   business operation;     memberships · idempotency_keys
  optimistic claim)  idempotency,  permission + workflow    CHECK constraints · GIN full-text index
                     commit once   rules enforced here      keyset-friendly indexes
```

**Work items** have a title, description (why it exists), status, priority, owner, due date, team,
creator, and a `version`. Statuses: `open → in_progress → pending_approval → resolved → closed`
(with reopen paths; see `backend/app/workflow.py`).

**Roles are per team:** `viewer` (read), `member` (create, comment, claim; modify what they own or
created), `manager` (everything in the team, assign anyone, approve/resolve, reopen closed items).

**History:** every create, edit (with field-level before/after), assignment, status change and
comment is an entry in an append-only `item_events` table, written in the same transaction as the change.

**Overview:** attention tiles (assigned to me, unassigned, awaiting my approval, overdue) computed
server-side; search (prefix-matching full-text on title and description, or `#id`); filters by team,
status, priority, owner; two sort orders; cursor pagination. Filters live in the URL, so views are
shareable and survive refresh.

**Correctness beyond CRUD** (details and trade-offs in ENGINEERING_DECISIONS.md):

| Situation | Mechanism |
|-----------|-----------|
| Two users claim the same item | single conditional `UPDATE ... WHERE owner_id IS NULL` |
| Editing from a stale copy | optimistic concurrency via `version`; `409` with current version |
| Double click / retry after timeout | `Idempotency-Key`, committed atomically with the change |
| Workflow and approval rules | pure `check_transition()` used by both enforcement and UI hints, plus DB `CHECK` backstop |
| Resource-level authorization | membership checked against the item's own team on every request; scoped inside list queries |
| Optimistic UI vs server decision | claim is optimistic with snapshot rollback; everything else waits for the server |

---

## Tests

```bash
cd backend && source .venv/bin/activate
pytest                     # 48 tests, ~8s, needs the opsdesk_test database

cd frontend
npm test                   # 5 tests: optimistic claim success + rollback, edit-conflict rebase
npm run build              # type-check + production build
```

The backend tests run against real PostgreSQL on purpose: the behaviours at risk (row-level
atomicity, unique-index blocking, `READ COMMITTED` re-checks) do not exist in SQLite.

What they cover, chosen by what would be most dangerous if wrong:

- **Concurrent claims:** 12 simultaneous claims from 3 users, exactly one winner, exactly one history entry.
- **Forced interleavings:** deterministic tests that hold two transactions open to reproduce the exact
  race window (stale write after a competing commit; second idempotent request blocking on the first),
  so they fail every time if a guard is removed. Thread-based tests alone can pass by luck.
- **Stale updates, no-op edits, comments not invalidating edits.**
- **Idempotency:** sequential replay, concurrent replay, key reused for a different request, failed
  attempt not consuming the key, keys scoped per user.
- **Authorization:** every action across roles; non-members get 404; list and search never leak other teams.
- **Workflow:** illegal jumps, owner required, approval gate, release/unassign resetting status, closed items frozen, DB constraint backstop.
- **History and listing:** attributed field-level history, pagination with no gaps/duplicates while new
  items arrive, prefix search, attention views.

I verified the concurrency tests can fail: temporarily removing the claim guard or the version guard
makes them go red.

---

## Known limitations

Deliberately out of scope for a one-day first version (reasoning in ENGINEERING_DECISIONS.md):

- **No asynchronous processing or notifications.** Nothing in the required behaviour needed it, and a
  half-built queue is worse than none. The design for adding it (transactional outbox) is described in the decisions doc.
- **No real-time push.** Screens poll (10-20 s) and refetch on window focus. The version check and
  the claim guard are the safety net for the window between polls.
- **No user/team/membership management.** Users, teams and roles come from the seed script.
- **No schema migrations.** Tables are created with `create_all`; a real deployment needs Alembic.
- **Auth is minimal:** JWT stored in `localStorage` (readable by any XSS), no refresh tokens, no
  revocation, no login rate limiting. Roles are read from the database on every request, so role
  changes apply immediately, but the UI's cached role list refreshes only on page load.
- **History is append-only by construction** (no code path updates or deletes it), not enforced by a database trigger.
- **Idempotency records are never purged**; production needs a retention job (e.g. 24-48 h).
- **Search** is English full-text with prefix matching: no typo tolerance, no relevance ranking (results
  keep the list's sort order so pagination stays stable).
- **Dashboard counts** are live `COUNT` queries. Fine for tens of thousands of active items;
  beyond that they would become maintained counters.
- **Multi-team list plan:** the "all my teams" list walks a team-less sort index and filters by team.
  This is fast when a user's teams hold a good share of rows, but a user in one tiny team out of thousands
  should use the single-team filter path (which has its own index).
- Frontend automated tests cover the two riskiest client behaviours (optimistic claim rollback, edit-conflict rebase). The rest of the UI is type-checked and built but has no automated browser tests.
