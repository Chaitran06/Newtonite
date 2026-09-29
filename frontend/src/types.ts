export type Status = "open" | "in_progress" | "pending_approval" | "resolved" | "closed";
export type Priority = "low" | "medium" | "high" | "urgent";

export const STATUSES: Status[] = ["open", "in_progress", "pending_approval", "resolved", "closed"];
export const ACTIVE_STATUSES: Status[] = ["open", "in_progress", "pending_approval"];
export const PRIORITIES: Priority[] = ["low", "medium", "high", "urgent"];

export const STATUS_LABEL: Record<Status, string> = {
  open: "Open",
  in_progress: "In progress",
  pending_approval: "Pending approval",
  resolved: "Resolved",
  closed: "Closed",
};

export interface UserRef {
  id: number;
  name: string;
}
export interface Membership {
  team_id: number;
  team_name: string;
  role: "viewer" | "member" | "manager";
}
export interface Me {
  id: number;
  name: string;
  email: string;
  memberships: Membership[];
}
export interface ItemSummary {
  id: number;
  team_id: number;
  team_name: string;
  title: string;
  status: Status;
  priority: Priority;
  owner: UserRef | null;
  created_by: UserRef;
  due_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}
export interface Permissions {
  role: string;
  can_edit: boolean;
  can_claim: boolean;
  can_release: boolean;
  can_assign: boolean;
  can_comment: boolean;
  transitions: Status[];
}
export interface ItemDetail extends ItemSummary {
  description: string;
  permissions: Permissions;
}
export interface ItemPage {
  items: ItemSummary[];
  next_cursor: string | null;
}
export interface ItemEvent {
  id: number;
  item_id: number;
  kind: "created" | "updated" | "status_changed" | "assigned" | "comment";
  actor: UserRef;
  data: Record<string, any>;
  created_at: string;
}
export interface EventPage {
  events: ItemEvent[];
  next_cursor: number | null;
}
export interface Dashboard {
  counts: { mine: number; unassigned: number; needs_approval: number; overdue: number };
  by_status: Record<string, number>;
  teams: Membership[];
}
export interface TeamMember {
  id: number;
  name: string;
  role: string;
}
