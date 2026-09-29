import type { ItemEvent, Priority, Status } from "../types";
import { STATUS_LABEL } from "../types";

export function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

export const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge status-${status}`}>{STATUS_LABEL[status]}</span>;
}

export function PriorityBadge({ priority }: { priority: Priority }) {
  return <span className={`badge prio-${priority}`}>{priority}</span>;
}

export function isOverdue(due: string | null, status: Status) {
  return !!due && new Date(due) < new Date() && ["open", "in_progress", "pending_approval"].includes(status);
}

/** One human sentence per history entry. */
export function describeEvent(e: ItemEvent): string {
  const d = e.data;
  switch (e.kind) {
    case "created":
      return "created this item";
    case "status_changed":
      return `moved it from ${STATUS_LABEL[d.from as Status] ?? d.from} to ${STATUS_LABEL[d.to as Status] ?? d.to}`;
    case "assigned":
      if (d.via === "claim") return "claimed it";
      if (d.via === "release") return "released it";
      if (!d.to) return `unassigned it${d.from ? ` (was ${d.from.name})` : ""}`;
      return `assigned it to ${d.to.name}${d.from ? ` (was ${d.from.name})` : ""}`;
    case "updated": {
      const parts = Object.entries(d.changes as Record<string, { from: any; to: any }>).map(([field, c]) => {
        if (field === "description") return "edited the description";
        if (field === "due_at") return `changed the due date to ${c.to ? new Date(c.to).toLocaleDateString() : "none"}`;
        return `changed ${field} from “${c.from}” to “${c.to}”`;
      });
      return parts.join("; ");
    }
    default:
      return e.kind;
  }
}
