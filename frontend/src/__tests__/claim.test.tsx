/**
 * Reconciling optimistic UI with the server's decision.
 * The UI shows "you own it" instantly; if the server says someone else won (409), the UI must
 * roll back to the previous state and tell the user who won.
 */
import { ReactNode } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { InfiniteData, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ItemDetail, ItemPage, ItemSummary } from "../types";

const toast = vi.fn();
vi.mock("../auth", () => ({ useAuth: () => ({ user: { id: 7, name: "Me", email: "me@x", memberships: [] } }) }));
vi.mock("../toast", () => ({ useToast: () => toast }));

import { useClaim } from "../hooks";

const summary: ItemSummary = {
  id: 5, team_id: 1, team_name: "Support", title: "Refund stuck", status: "open", priority: "high",
  owner: null, created_by: { id: 2, name: "Bob" }, due_at: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", version: 1,
};
const filters = { q: "", team: "", status: ["open"], priority: "", owner: "", view: "", sort: "attention" };

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } });
  const data: InfiniteData<ItemPage> = { pages: [{ items: [summary], next_cursor: null }], pageParams: [null] };
  qc.setQueryData(["items", filters], data);
  qc.setQueryData(["item", 5], {
    ...summary, description: "", permissions: { role: "member", can_edit: false, can_claim: true, can_release: false,
      can_assign: false, can_comment: true, transitions: [] },
  } satisfies ItemDetail);
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  return { qc, wrapper };
}
const listOwner = (qc: QueryClient) => qc.getQueryData<InfiniteData<ItemPage>>(["items", filters])!.pages[0].items[0].owner;

beforeEach(() => toast.mockClear());

describe("optimistic claim", () => {
  it("shows the claim immediately, then rolls back and explains when the server says someone else won", async () => {
    let respond!: (r: Response) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((res) => (respond = res))));
    const { qc, wrapper } = setup();
    const { result } = renderHook(() => useClaim(), { wrapper });

    act(() => result.current.mutate({ id: 5 }));

    // 1. Optimistic: before the server answered, both the list row and the detail view say "Me".
    await waitFor(() => expect(listOwner(qc)?.name).toBe("Me"));
    expect(qc.getQueryData<ItemDetail>(["item", 5])?.owner?.name).toBe("Me");
    expect(qc.getQueryData<ItemDetail>(["item", 5])?.permissions.can_claim).toBe(false);

    // 2. Server decision: 409, Bob got there first.
    respond(new Response(JSON.stringify({ error: { code: "already_claimed", message: "Bob has already claimed this item", owner: { id: 2, name: "Bob" } } }),
      { status: 409, headers: { "content-type": "application/json" } }));

    // 3. Rolled back to the exact previous state, and the user is told why.
    await waitFor(() => expect(listOwner(qc)).toBeNull());
    expect(qc.getQueryData<ItemDetail>(["item", 5])?.owner).toBeNull();
    expect(qc.getQueryData<ItemDetail>(["item", 5])?.permissions.can_claim).toBe(true);
    expect(toast).toHaveBeenCalledWith("error", expect.stringContaining("Bob"));
  });

  it("keeps the claim and adopts the server's copy on success", async () => {
    const serverCopy = { ...summary, owner: { id: 7, name: "Me" }, status: "in_progress", version: 2, description: "",
      permissions: { role: "member", can_edit: true, can_claim: false, can_release: true, can_assign: false, can_comment: true, transitions: [] } };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(serverCopy), { status: 200, headers: { "content-type": "application/json" } })));
    const { qc, wrapper } = setup();
    const { result } = renderHook(() => useClaim(), { wrapper });

    await act(async () => { await result.current.mutateAsync({ id: 5 }); });

    expect(qc.getQueryData<ItemDetail>(["item", 5])?.version).toBe(2); // server's authoritative copy
    expect(qc.getQueryData<ItemDetail>(["item", 5])?.permissions.can_release).toBe(true);
    expect(toast).not.toHaveBeenCalled();
  });
});
