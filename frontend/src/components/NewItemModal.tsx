import { FormEvent, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError, newKey } from "../api";
import { useAuth } from "../auth";
import { useToast } from "../toast";
import type { ItemDetail, Priority } from "../types";
import { PRIORITIES } from "../types";

export default function NewItemModal({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const writable = user!.memberships.filter((m) => m.role !== "viewer");

  const [teamId, setTeamId] = useState(String(writable[0]?.team_id ?? ""));
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<Priority>("medium");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // One key for this form session. If the request times out and the user clicks Create again,
  // the SAME key is sent, so the server returns the first result instead of creating a duplicate.
  const key = useRef(newKey());

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return; // ignore double clicks while a request is in flight
    setBusy(true);
    setError(null);
    try {
      const item = await api<ItemDetail>("/items", {
        method: "POST",
        idempotencyKey: key.current,
        body: {
          team_id: Number(teamId),
          title,
          description,
          priority,
          due_at: due ? new Date(due).toISOString() : null,
        },
      });
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast("success", `Created #${item.id}`);
      onCreated(item.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the item");
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) key.current = newKey(); // request was rejected; a corrected form is a new request
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <form className="card modal" onMouseDown={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2>New work item</h2>
        <label>
          Team
          <select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
            {writable.map((m) => (
              <option key={m.team_id} value={m.team_id}>
                {m.team_name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Title — what needs to happen?
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required autoFocus />
        </label>
        <label>
          Why does it exist? (context, links, what has been tried)
          <textarea rows={5} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <div className="row">
          <label>
            Priority
            <select value={priority} onChange={(e) => setPriority(e.target.value as Priority)}>
              {PRIORITIES.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <label>
            Due (optional)
            <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
          </label>
        </div>
        {error && <div className="error">{error}</div>}
        <div className="row end">
          <button type="button" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy || !title.trim() || !teamId}>
            {busy ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}
