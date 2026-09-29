import { FormEvent, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, newKey } from "../api";
import { applyServerItem, refreshItemData, useClaim, useEvents, useItem, useMembers } from "../hooks";
import { rebaseDraft } from "../rebase";
import { useToast } from "../toast";
import type { ItemDetail, Priority, Status } from "../types";
import { PRIORITIES, STATUS_LABEL } from "../types";
import { describeEvent, fmt, isOverdue, PriorityBadge, StatusBadge, timeAgo } from "../components/ui";

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function transitionLabel(from: Status, to: Status): string {
  if (to === "resolved") return from === "pending_approval" ? "Approve" : "Mark resolved";
  if (to === "in_progress") return from === "pending_approval" ? "Reject (back to in progress)" : "Start work";
  if (to === "pending_approval") return "Request approval";
  if (to === "closed") return "Close";
  return from === "closed" || from === "resolved" ? "Reopen" : "Move back to open";
}

/** "What requires attention next", derived from the item's state. */
function nextStep(item: ItemDetail): string {
  if (isOverdue(item.due_at, item.status)) return "Overdue — needs attention now.";
  switch (item.status) {
    case "open":
      return item.owner ? `Assigned to ${item.owner.name}; work has not started.` : "Needs an owner — claim it or ask a manager to assign it.";
    case "in_progress":
      return `In progress with ${item.owner?.name ?? "—"}.`;
    case "pending_approval":
      return "Waiting for a team manager to approve or reject.";
    case "resolved":
      return "Resolved. Close it once the outcome is confirmed.";
    case "closed":
      return "Closed. Only a manager can reopen it.";
  }
}

export default function ItemPage() {
  const itemId = Number(useParams().id);
  const q = useItem(itemId);
  const item = q.data;

  if (q.isLoading) return <div className="page"><p className="muted">Loading…</p></div>;
  if (q.error || !item) {
    const notFound = q.error instanceof ApiError && q.error.status === 404;
    return (
      <div className="page">
        <Link to="/">← Back</Link>
        <h2>{notFound ? "Not found" : "Could not load this item"}</h2>
        <p className="muted">
          {notFound ? "It does not exist, or you are not a member of its team." : (q.error as Error)?.message}
        </p>
      </div>
    );
  }
  return <ItemView key={item.id} item={item} />;
}

function ItemView({ item }: { item: ItemDetail }) {
  const qc = useQueryClient();
  const toast = useToast();
  const claim = useClaim();
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState("");
  const perms = item.permissions;
  const members = useMembers(item.team_id, perms.can_assign);

  // All non-optimistic actions: wait for the server's verdict, then show its authoritative copy.
  const action = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      api<ItemDetail>(`/items/${item.id}${path}`, { method: "POST", body, idempotencyKey: newKey() }),
    onSuccess: (updated) => {
      applyServerItem(qc, updated);
      qc.invalidateQueries({ queryKey: ["events", item.id] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      setNote("");
    },
    onError: (err) => {
      const stale = err instanceof ApiError && err.code === "stale_version";
      toast("error", stale ? "This item changed while you were looking at it. It has been refreshed — please review and try again." : (err as Error).message);
      refreshItemData(qc, item.id); // whatever went wrong, our copy is suspect: refetch the truth
    },
  });

  return (
    <div className="page">
      <header className="topbar">
        <Link to="/">← All work</Link>
        <span className="grow" />
        <span className="muted small">
          #{item.id} · {item.team_name}
        </span>
      </header>

      <div className="detail">
        <main>
          <div className={`next ${isOverdue(item.due_at, item.status) ? "danger" : ""}`}>
            <strong>Next:</strong> {nextStep(item)}
          </div>

          {editing ? (
            <EditPanel item={item} onDone={() => setEditing(false)} />
          ) : (
            <>
              <h1>{item.title}</h1>
              <p className="description">{item.description || <span className="muted">No description.</span>}</p>
              {perms.can_edit && (
                <button className="small" onClick={() => setEditing(true)}>
                  Edit
                </button>
              )}
            </>
          )}

          <Timeline item={item} />
        </main>

        <aside className="card side">
          <dl>
            <dt>Status</dt>
            <dd>
              <StatusBadge status={item.status} />
            </dd>
            <dt>Priority</dt>
            <dd>
              <PriorityBadge priority={item.priority} />
            </dd>
            <dt>Owner</dt>
            <dd>
              {item.owner ? item.owner.name : <span className="muted">Nobody</span>}
              {perms.can_claim && (
                <button className="small primary" disabled={claim.isPending} onClick={() => claim.mutate({ id: item.id })}>
                  {claim.isPending ? "Claiming…" : "Claim"}
                </button>
              )}
              {perms.can_release && (
                <button className="small" disabled={action.isPending} onClick={() => action.mutate({ path: "/release" })}>
                  Release
                </button>
              )}
            </dd>
            {perms.can_assign && (
              <>
                <dt>Assign</dt>
                <dd>
                  <select
                    value={item.owner?.id ?? ""}
                    disabled={action.isPending}
                    onChange={(e) =>
                      action.mutate({
                        path: "/assign",
                        body: { expected_version: item.version, user_id: e.target.value ? Number(e.target.value) : null },
                      })
                    }
                  >
                    <option value="">Unassigned</option>
                    {members.data?.filter((m) => m.role !== "viewer").map((m) => (
                      <option key={m.id} value={m.id}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </dd>
              </>
            )}
            <dt>Due</dt>
            <dd className={isOverdue(item.due_at, item.status) ? "overdue" : ""}>{fmt(item.due_at)}</dd>
            <dt>Created by</dt>
            <dd>
              {item.created_by.name} <span className="muted small">{timeAgo(item.created_at)}</span>
            </dd>
            <dt>Last activity</dt>
            <dd>{timeAgo(item.updated_at)}</dd>
          </dl>

          {perms.transitions.length > 0 && (
            <div className="actions">
              <h3>Move this item</h3>
              <input placeholder="Note (optional, saved in history)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
              {perms.transitions.map((to) => (
                <button
                  key={to}
                  className={to === "resolved" || to === "in_progress" ? "primary" : ""}
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate({ path: "/transition", body: { expected_version: item.version, to, note: note.trim() || null } })
                  }
                  title={`→ ${STATUS_LABEL[to]}`}
                >
                  {transitionLabel(item.status, to)}
                </button>
              ))}
            </div>
          )}
          {perms.transitions.length === 0 && item.status !== "closed" && (
            <p className="muted small">Your role ({perms.role}) cannot move this item right now.</p>
          )}
        </aside>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------- editing

function EditPanel({ item, onDone }: { item: ItemDetail; onDone: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const initial = useRef({ title: item.title, description: item.description, priority: item.priority, due: toLocalInput(item.due_at) });
  const [draft, setDraft] = useState({ ...initial.current });
  // The version our edits are based on. The server rejects the save if the item moved on.
  const [baseVersion, setBaseVersion] = useState(item.version);
  const [conflict, setConflict] = useState(false);
  const movedOn = item.version !== baseVersion; // polling brought in someone else's change

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = { expected_version: baseVersion };
      if (draft.title !== initial.current.title) body.title = draft.title;
      if (draft.description !== initial.current.description) body.description = draft.description;
      if (draft.priority !== initial.current.priority) body.priority = draft.priority;
      if (draft.due !== initial.current.due) body.due_at = draft.due ? new Date(draft.due).toISOString() : null;
      return api<ItemDetail>(`/items/${item.id}`, { method: "PATCH", body, idempotencyKey: newKey() });
    },
    onSuccess: (updated) => {
      applyServerItem(qc, updated);
      qc.invalidateQueries({ queryKey: ["events", item.id] });
      onDone();
    },
    onError: (err) => {
      if (err instanceof ApiError && err.code === "stale_version") {
        setConflict(true);
        refreshItemData(qc, item.id); // pull the latest so we can show what changed
      } else {
        toast("error", (err as Error).message);
      }
    },
  });

  const serverNow = { title: item.title, description: item.description, priority: item.priority, due: toLocalInput(item.due_at) };
  const hint = (field: keyof typeof serverNow) =>
    (conflict || movedOn) && serverNow[field] !== initial.current[field] ? (
      <small className="warn">Now on the server: “{String(serverNow[field]) || "empty"}”</small>
    ) : null;

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate();
  }

  // "Keep my edits on top of the latest version": only my own changes survive; fields I did not
  // touch take the server's current value (see rebase.ts).
  function rebase() {
    setDraft(rebaseDraft(initial.current, draft, serverNow));
    initial.current = serverNow;
    setBaseVersion(item.version);
    setConflict(false);
  }

  return (
    <form onSubmit={submit} className="edit">
      {(conflict || movedOn) && (
        <div className="warn-box">
          <strong>{conflict ? "Your changes were not saved." : "Heads up:"}</strong> Someone else changed this item
          (now version {item.version}, you started from {baseVersion}). Review the values marked below.
          <div className="row">
            <button type="button" onClick={rebase}>
              Keep my edits on top of the latest version
            </button>
            <button type="button" onClick={onDone}>
              Discard my edits
            </button>
          </div>
        </div>
      )}
      <label>
        Title
        <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} maxLength={200} required />
        {hint("title")}
      </label>
      <label>
        Description
        <textarea rows={6} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
        {hint("description")}
      </label>
      <div className="row">
        <label>
          Priority
          <select value={draft.priority} onChange={(e) => setDraft({ ...draft, priority: e.target.value as Priority })}>
            {PRIORITIES.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
          {hint("priority")}
        </label>
        <label>
          Due
          <input type="datetime-local" value={draft.due} onChange={(e) => setDraft({ ...draft, due: e.target.value })} />
          {hint("due")}
        </label>
      </div>
      <div className="row">
        <button className="primary" disabled={save.isPending || !draft.title.trim()}>
          {save.isPending ? "Saving…" : "Save"}
        </button>
        <button type="button" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------------------- history

function Timeline({ item }: { item: ItemDetail }) {
  const qc = useQueryClient();
  const events = useEvents(item.id);
  const [body, setBody] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Same idea as the create form: one key per composed comment, reused if the user re-submits.
  const key = useRef(newKey());

  const post = useMutation({
    mutationFn: () => api(`/items/${item.id}/comments`, { method: "POST", body: { body }, idempotencyKey: key.current }),
    onSuccess: () => {
      setBody("");
      setError(null);
      key.current = newKey();
      qc.invalidateQueries({ queryKey: ["events", item.id] });
      qc.invalidateQueries({ queryKey: ["items"] });
    },
    onError: (err) => setError((err as Error).message),
  });

  const all = events.data?.pages.flatMap((p) => p.events) ?? [];
  return (
    <section>
      <h2>Activity</h2>
      {item.permissions.can_comment ? (
        <form
          className="comment-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (body.trim() && !post.isPending) post.mutate();
          }}
        >
          <textarea rows={3} placeholder="Add a comment…" value={body} onChange={(e) => setBody(e.target.value)} />
          {error && <div className="error">{error}</div>}
          <button className="primary" disabled={!body.trim() || post.isPending}>
            {post.isPending ? "Posting…" : "Comment"}
          </button>
        </form>
      ) : (
        <p className="muted small">Your role ({item.permissions.role}) can read but not comment.</p>
      )}

      <ol className="timeline">
        {all.map((e) => (
          <li key={e.id} className={e.kind}>
            <div className="meta">
              <strong>{e.actor.name}</strong> {e.kind === "comment" ? "commented" : describeEvent(e)}
              <span className="muted small" title={fmt(e.created_at)}>
                {" "}
                · {timeAgo(e.created_at)}
              </span>
            </div>
            {e.kind === "comment" && <p className="comment-body">{e.data.body}</p>}
            {e.kind === "status_changed" && e.data.note && <p className="note">“{e.data.note}”</p>}
          </li>
        ))}
      </ol>
      {events.hasNextPage && (
        <button onClick={() => events.fetchNextPage()} disabled={events.isFetchingNextPage}>
          {events.isFetchingNextPage ? "Loading…" : "Load older activity"}
        </button>
      )}
    </section>
  );
}
