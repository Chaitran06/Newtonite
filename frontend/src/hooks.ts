import {
  InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  QueryClient,
} from "@tanstack/react-query";
import { api, ApiError, newKey } from "./api";
import { useAuth } from "./auth";
import { useToast } from "./toast";
import type {
  Dashboard,
  EventPage,
  ItemDetail,
  ItemPage,
  ItemSummary,
  Status,
  TeamMember,
} from "./types";
import { ACTIVE_STATUSES } from "./types";

// ------------------------------------------------------------------ list filters (live in the URL)

export interface Filters {
  q: string;
  team: string;
  status: Status[];
  priority: string;
  owner: string;
  view: string;
  sort: "attention" | "recent";
}

export function parseFilters(p: URLSearchParams): Filters {
  const status = p.get("status");
  return {
    q: p.get("q") ?? "",
    team: p.get("team") ?? "",
    status: status ? (status.split(",").filter(Boolean) as Status[]) : ACTIVE_STATUSES,
    priority: p.get("priority") ?? "",
    owner: p.get("owner") ?? "",
    view: p.get("view") ?? "",
    sort: p.get("sort") === "recent" ? "recent" : "attention",
  };
}

function itemsQuery(f: Filters, cursor: string | null): string {
  const p = new URLSearchParams();
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.team) p.set("team_id", f.team);
  f.status.forEach((s) => p.append("status", s));
  if (f.priority) p.append("priority", f.priority);
  if (f.owner) p.set("owner", f.owner);
  if (f.view) p.set("view", f.view);
  p.set("sort", f.sort);
  p.set("limit", "25");
  if (cursor) p.set("cursor", cursor);
  return p.toString();
}

// ------------------------------------------------------------------ queries

export function useDashboard() {
  return useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api<Dashboard>("/dashboard"),
    refetchInterval: 20_000,
  });
}

export function useItems(filters: Filters) {
  return useInfiniteQuery({
    queryKey: ["items", filters],
    queryFn: ({ pageParam }) => api<ItemPage>(`/items?${itemsQuery(filters, pageParam)}`),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: 20_000, // cheap staleness bound; also refetches on window focus
    placeholderData: (prev) => prev, // keep old rows on screen while a new filter loads
  });
}

export function useItem(id: number) {
  return useQuery({
    queryKey: ["item", id],
    queryFn: () => api<ItemDetail>(`/items/${id}`),
    refetchInterval: 10_000,
  });
}

export function useEvents(id: number) {
  return useInfiniteQuery({
    queryKey: ["events", id],
    queryFn: ({ pageParam }) =>
      api<EventPage>(`/items/${id}/events?limit=20${pageParam ? `&cursor=${pageParam}` : ""}`),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: 10_000,
  });
}

export function useMembers(teamId: number | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ["members", teamId],
    queryFn: () => api<TeamMember[]>(`/teams/${teamId}/members`),
    enabled: enabled && teamId !== undefined,
    staleTime: 60_000,
  });
}

// ------------------------------------------------------------------ cache helpers

export function refreshItemData(qc: QueryClient, id: number) {
  qc.invalidateQueries({ queryKey: ["item", id] });
  qc.invalidateQueries({ queryKey: ["events", id] });
  qc.invalidateQueries({ queryKey: ["items"] });
  qc.invalidateQueries({ queryKey: ["dashboard"] });
}

/** Write the server's authoritative copy into every cache that shows this item. */
export function applyServerItem(qc: QueryClient, item: ItemDetail) {
  qc.setQueryData(["item", item.id], item);
  patchInLists(qc, item.id, () => ({ ...item }));
}

type Lists = [readonly unknown[], InfiniteData<ItemPage> | undefined][];

function patchInLists(qc: QueryClient, id: number, patch: (i: ItemSummary) => ItemSummary) {
  qc.setQueriesData<InfiniteData<ItemPage>>({ queryKey: ["items"] }, (old) =>
    old
      ? {
          ...old,
          pages: old.pages.map((p) => ({ ...p, items: p.items.map((i) => (i.id === id ? patch(i) : i)) })),
        }
      : old,
  );
}

// ------------------------------------------------------------------ optimistic claim

/**
 * Claim is the one place we are OPTIMISTIC: the UI immediately shows "you own it", because
 * the common case succeeds and the button feels instant. But the server is the judge:
 *   - success  -> replace our guess with the server's copy
 *   - 409 lost -> roll back to the snapshot, tell the user who won, refetch the truth
 * Every other mutation (edits, status changes) waits for the server, because their outcome
 * depends on rules we do not want to duplicate client-side.
 */
export function useClaim() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const toast = useToast();

  return useMutation({
    mutationFn: ({ id }: { id: number }) =>
      api<ItemDetail>(`/items/${id}/claim`, { method: "POST", idempotencyKey: newKey() }),

    onMutate: async ({ id }) => {
      await qc.cancelQueries({ queryKey: ["items"] });
      await qc.cancelQueries({ queryKey: ["item", id] });
      const lists = qc.getQueriesData<InfiniteData<ItemPage>>({ queryKey: ["items"] }) as Lists;
      const detail = qc.getQueryData<ItemDetail>(["item", id]);

      const me = { id: user!.id, name: user!.name };
      patchInLists(qc, id, (i) => ({ ...i, owner: me, status: "in_progress" }));
      if (detail) {
        qc.setQueryData<ItemDetail>(["item", id], {
          ...detail,
          owner: me,
          status: "in_progress",
          permissions: { ...detail.permissions, can_claim: false, can_release: true },
        });
      }
      return { lists, detail };
    },

    onSuccess: (item) => applyServerItem(qc, item),

    onError: (err, { id }, ctx) => {
      // Roll back our optimistic guess.
      ctx?.lists.forEach(([key, data]) => qc.setQueryData(key, data));
      if (ctx?.detail) qc.setQueryData(["item", id], ctx.detail);
      if (err instanceof ApiError && err.code === "already_claimed") {
        toast("error", `Too late: ${err.extra.owner?.name ?? "someone else"} claimed this item first.`);
      } else {
        toast("error", err instanceof Error ? err.message : "Could not claim the item");
      }
    },

    onSettled: (_d, _e, { id }) => refreshItemData(qc, id),
  });
}
