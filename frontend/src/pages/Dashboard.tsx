import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import { parseFilters, useClaim, useDashboard, useItems } from "../hooks";
import { ACTIVE_STATUSES, PRIORITIES, STATUSES, STATUS_LABEL } from "../types";
import type { Status } from "../types";
import { isOverdue, PriorityBadge, StatusBadge, timeAgo } from "../components/ui";
import NewItemModal from "../components/NewItemModal";

const VIEWS: { key: string; label: string; hint: string }[] = [
  { key: "mine", label: "Assigned to me", hint: "Work you own that is not finished" },
  { key: "unassigned", label: "Unassigned", hint: "Open items nobody owns yet" },
  { key: "needs_approval", label: "Awaiting my approval", hint: "Items in teams you manage" },
  { key: "overdue", label: "Overdue", hint: "Past due date and still active" },
];

export default function Dashboard() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const filters = parseFilters(params);
  const [showNew, setShowNew] = useState(false);

  // Search box is local state, debounced into the URL so we do not query on every keystroke.
  const [search, setSearch] = useState(filters.q);
  useEffect(() => {
    const t = setTimeout(() => {
      if (search !== filters.q) update({ q: search });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  function update(patch: Record<string, string | null>) {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    setParams(next, { replace: true });
  }

  const dash = useDashboard();
  const items = useItems(filters);
  const claim = useClaim();
  const roleByTeam = new Map(user!.memberships.map((m) => [m.team_id, m.role]));
  const canCreate = user!.memberships.some((m) => m.role !== "viewer");
  const rows = items.data?.pages.flatMap((p) => p.items) ?? [];

  const toggleStatus = (s: Status) => {
    const next = filters.status.includes(s) ? filters.status.filter((x) => x !== s) : [...filters.status, s];
    const isDefault = next.length === ACTIVE_STATUSES.length && ACTIVE_STATUSES.every((a) => next.includes(a));
    update({ status: isDefault ? null : next.join(",") });
  };

  return (
    <div className="page">
      <header className="topbar">
        <strong className="brand">OpsDesk</strong>
        <span className="grow" />
        <span className="muted">{user!.name}</span>
        <button onClick={logout}>Sign out</button>
      </header>

      <section className="attention">
        {VIEWS.map((v) => {
          const count = dash.data?.counts[v.key as keyof typeof dash.data.counts];
          const active = filters.view === v.key;
          return (
            <button
              key={v.key}
              className={`tile ${active ? "active" : ""} ${count ? "has" : ""}`}
              title={v.hint}
              onClick={() => update({ view: active ? null : v.key })}
            >
              <span className="count">{count ?? "–"}</span>
              <span>{v.label}</span>
            </button>
          );
        })}
      </section>

      <section className="filters">
        <input
          className="search"
          placeholder="Search title and description…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={filters.team} onChange={(e) => update({ team: e.target.value || null })} aria-label="Team">
          <option value="">All my teams</option>
          {user!.memberships.map((m) => (
            <option key={m.team_id} value={m.team_id}>
              {m.team_name}
            </option>
          ))}
        </select>
        <select value={filters.priority} onChange={(e) => update({ priority: e.target.value || null })} aria-label="Priority">
          <option value="">Any priority</option>
          {PRIORITIES.map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
        <select value={filters.owner} onChange={(e) => update({ owner: e.target.value || null })} aria-label="Owner">
          <option value="">Any owner</option>
          <option value="me">Me</option>
          <option value="none">Unassigned</option>
        </select>
        <select value={filters.sort} onChange={(e) => update({ sort: e.target.value === "attention" ? null : e.target.value })} aria-label="Sort">
          <option value="attention">Sort: needs attention</option>
          <option value="recent">Sort: recent activity</option>
        </select>
        <span className="grow" />
        {canCreate && (
          <button className="primary" onClick={() => setShowNew(true)}>
            + New item
          </button>
        )}
      </section>

      <section className="chips">
        {STATUSES.map((s) => (
          <button key={s} className={`chip ${filters.status.includes(s) ? "on" : ""}`} onClick={() => toggleStatus(s)}>
            {STATUS_LABEL[s]}
            {dash.data?.by_status[s] !== undefined && <small> {dash.data.by_status[s]}</small>}
          </button>
        ))}
        {(filters.view || filters.q || filters.team || filters.priority || filters.owner || params.get("status")) && (
          <button className="link" onClick={() => { setSearch(""); setParams({}, { replace: true }); }}>
            Clear filters
          </button>
        )}
      </section>

      {items.isError && (
        <div className="error">
          Could not load items. <button onClick={() => items.refetch()}>Retry</button>
        </div>
      )}

      <table className="list">
        <thead>
          <tr>
            <th>#</th>
            <th>Title</th>
            <th>Team</th>
            <th>Priority</th>
            <th>Status</th>
            <th>Owner</th>
            <th>Due</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((i) => {
            const role = roleByTeam.get(i.team_id);
            const claimable = i.owner === null && i.status === "open" && role && role !== "viewer";
            const overdue = isOverdue(i.due_at, i.status);
            return (
              <tr key={i.id} onClick={() => navigate(`/items/${i.id}`)} className="clickable">
                <td className="muted">{i.id}</td>
                <td>
                  <Link to={`/items/${i.id}`} onClick={(e) => e.stopPropagation()}>
                    {i.title}
                  </Link>
                </td>
                <td>{i.team_name}</td>
                <td>
                  <PriorityBadge priority={i.priority} />
                </td>
                <td>
                  <StatusBadge status={i.status} />
                </td>
                <td>
                  {i.owner ? (
                    i.owner.name
                  ) : claimable ? (
                    <button
                      className="small primary"
                      disabled={claim.isPending && claim.variables?.id === i.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        claim.mutate({ id: i.id });
                      }}
                    >
                      Claim
                    </button>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td className={overdue ? "overdue" : "muted"}>{i.due_at ? new Date(i.due_at).toLocaleDateString() : "—"}</td>
                <td className="muted">{timeAgo(i.updated_at)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {items.isLoading && <p className="muted center">Loading…</p>}
      {!items.isLoading && rows.length === 0 && <p className="muted center">Nothing matches these filters.</p>}
      {items.hasNextPage && (
        <div className="center">
          <button onClick={() => items.fetchNextPage()} disabled={items.isFetchingNextPage}>
            {items.isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
      <p className="muted small center">{rows.length} shown{items.hasNextPage ? " — more available" : ""}</p>

      {showNew && (
        <NewItemModal
          onClose={() => setShowNew(false)}
          onCreated={(id) => {
            setShowNew(false);
            navigate(`/items/${id}`);
          }}
        />
      )}
    </div>
  );
}
