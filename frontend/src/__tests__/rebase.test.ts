import { describe, expect, it } from "vitest";
import { rebaseDraft } from "../rebase";

const started = { title: "Refund stuck", description: "details", priority: "medium", due: "" };

describe("rebaseDraft", () => {
  it("keeps my edits and adopts the server's value for fields I did not touch", () => {
    // I only changed the title. Meanwhile someone raised the priority to urgent.
    const draft = { ...started, title: "Refund stuck for VIP customer" };
    const server = { ...started, priority: "urgent" };

    const next = rebaseDraft(started, draft, server);

    expect(next.title).toBe("Refund stuck for VIP customer"); // my change survives
    expect(next.priority).toBe("urgent"); // their change is NOT reverted
  });

  it("when we both changed the same field, my value wins (and the user was warned before this)", () => {
    const draft = { ...started, priority: "low" };
    const server = { ...started, priority: "urgent" };
    expect(rebaseDraft(started, draft, server).priority).toBe("low");
  });

  it("with no local edits, the result is exactly the server's copy", () => {
    const server = { ...started, title: "Renamed by someone", priority: "high" };
    expect(rebaseDraft(started, { ...started }, server)).toEqual(server);
  });
});
