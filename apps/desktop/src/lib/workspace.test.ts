import { beforeEach, describe, expect, it, vi } from "vitest";

import { readWorkspace, WORKSPACE_KEY, writeWorkspace } from "@/lib/workspace";

describe("workspace preference", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("has no default, because guessing one would widen where the agent may write", () => {
    expect(readWorkspace()).toBe("");
  });

  it("remembers the chosen root across a launch", () => {
    writeWorkspace("/home/me/projects/thing");
    expect(readWorkspace()).toBe("/home/me/projects/thing");
    expect(localStorage.getItem(WORKSPACE_KEY)).toBe("/home/me/projects/thing");
  });

  it("clears rather than storing an empty string", () => {
    // "no choice" must round-trip as ABSENCE. Storing "" reads the same on the way out but is a
    // different statement, and the difference matters the day someone inspects what was chosen.
    writeWorkspace("/tmp/x");
    writeWorkspace("");

    expect(localStorage.getItem(WORKSPACE_KEY)).toBeNull();
    expect(readWorkspace()).toBe("");
  });

  it("survives storage being unavailable", () => {
    // A sandboxed webview or private mode. A preference is never worth throwing over — and this
    // one is read during render, so a throw would take the whole Code screen down with it.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("denied");
    });

    expect(() => writeWorkspace("/x")).not.toThrow();
    expect(readWorkspace()).toBe("");
  });
});
