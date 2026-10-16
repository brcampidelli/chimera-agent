import { beforeEach, describe, expect, it } from "vitest";

import { CLOSED_LIMIT, forgetClosed, rememberClosed, takeClosed } from "@/lib/closedConversations";

const all = () => true;

describe("the closed-conversation stack", () => {
  beforeEach(forgetClosed);

  it("reopens the most recently closed first", () => {
    rememberClosed({ id: "a", workspace: "/w" });
    rememberClosed({ id: "b", workspace: "/w" });
    expect(takeClosed(all, null)?.id).toBe("b");
    expect(takeClosed(all, null)?.id).toBe("a");
    expect(takeClosed(all, null)).toBeNull();
  });

  it("passes over one deleted since, and the one already on screen", () => {
    rememberClosed({ id: "gone", workspace: "/w" });
    rememberClosed({ id: "a", workspace: "/w" });
    rememberClosed({ id: "here", workspace: "/w" });
    rememberClosed({ id: "deleted", workspace: "/w" });
    const exists = (id: string) => id !== "deleted" && id !== "gone";
    expect(takeClosed(exists, "here")?.id).toBe("a");
    expect(takeClosed(exists, "here")).toBeNull();
  });

  it("holds a conversation once, at the top, however often it was left", () => {
    rememberClosed({ id: "a", workspace: "/w" });
    rememberClosed({ id: "b", workspace: "/w" });
    rememberClosed({ id: "a", workspace: "/w" });
    expect(takeClosed(all, null)?.id).toBe("a");
    expect(takeClosed(all, null)?.id).toBe("b");
    expect(takeClosed(all, null)).toBeNull();
  });

  it("keeps the newest few dozen, not every conversation of the sitting", () => {
    for (let i = 0; i < CLOSED_LIMIT + 5; i++) rememberClosed({ id: `c${i}`, workspace: "/w" });
    let n = 0;
    while (takeClosed(all, null)) n++;
    expect(n).toBe(CLOSED_LIMIT);
  });
});
