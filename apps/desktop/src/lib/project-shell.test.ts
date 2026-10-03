import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  SHELL_KEY,
  SHELL_MOVED_KEY,
  legacyShellGrants,
  migrateShellGrantsOnce,
  shellGranted,
} from "@/lib/project-shell";
import { sidebarOrder } from "@/lib/projects";

const migrateShellGrants = vi.fn();

vi.mock("@/lib/api", () => ({
  migrateShellGrants: (...args: unknown[]) => migrateShellGrants(...args),
}));

/**
 * Which projects the agent may run commands in — read from the server, never asserted.
 *
 * The grant used to live in this webview's storage and reach the server as a claim on every
 * request, which the server could not check. It now lives in the server's project registry and the
 * turn is held to it there, so this side has two jobs only: hand the old list over ONCE, and read
 * the server's answer. The earlier tests of this file checked a local store that granted; they
 * are replaced, not loosened — there is no local store that grants any more.
 */
describe("command grants, after the move to the server", () => {
  beforeEach(() => {
    localStorage.clear();
    migrateShellGrants.mockReset().mockResolvedValue({ migrated: true, recorded: 0, projects: [] });
  });

  it("reads a grant only from the server's rows, for that exact project", () => {
    const rows = [
      { path: "/projects/a", alias: "", shell_granted: true },
      { path: "/projects/b", alias: "" },
    ];
    expect(shellGranted(rows, "/projects/a")).toBe(true);
    // The whole point: granting one folder is not granting the next one opened.
    expect(shellGranted(rows, "/projects/b")).toBe(false);
    expect(shellGranted(rows, "/projects/c")).toBe(false);
  });

  it("says no with no project, and before the server has answered", () => {
    expect(shellGranted([{ path: "", alias: "", shell_granted: true }], "")).toBe(false);
    expect(shellGranted(undefined, "/projects/a")).toBe(false);
  });

  it("ignores what this browser still remembers once the grant moved", () => {
    // An old grant left in storage is migration input, not a permission. The server's empty answer
    // is the answer.
    localStorage.setItem(SHELL_KEY, JSON.stringify(["/projects/a"]));
    expect(shellGranted([], "/projects/a")).toBe(false);
  });

  it("hands the old grants to the server once, and the second load sends nothing", async () => {
    localStorage.setItem(SHELL_KEY, JSON.stringify(["/projects/a", "/projects/b"]));

    await migrateShellGrantsOnce();
    await migrateShellGrantsOnce();

    expect(migrateShellGrants.mock.calls).toEqual([[["/projects/a", "/projects/b"]]]);
    expect(localStorage.getItem(SHELL_MOVED_KEY)).toBe("1");
  });

  it("sends an empty hand-over on a fresh install, so the server closes the window", async () => {
    await migrateShellGrantsOnce();
    expect(migrateShellGrants.mock.calls).toEqual([[[]]]);
  });

  it("tries again next load when the server did not take it", async () => {
    migrateShellGrants.mockRejectedValueOnce(new Error("server not up yet"));
    localStorage.setItem(SHELL_KEY, JSON.stringify(["/projects/a"]));

    await expect(migrateShellGrantsOnce()).resolves.toBeUndefined();
    expect(localStorage.getItem(SHELL_MOVED_KEY)).toBeNull();

    await migrateShellGrantsOnce();
    expect(migrateShellGrants).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem(SHELL_MOVED_KEY)).toBe("1");
  });

  it("reads a corrupt old store as nothing to move", () => {
    localStorage.setItem(SHELL_KEY, "{not json");
    expect(legacyShellGrants()).toEqual([]);
    localStorage.setItem(SHELL_KEY, '{"a": true}');
    expect(legacyShellGrants()).toEqual([]);
  });
});

describe("the sidebar's order", () => {
  const conversas = [
    { workspace: "/b", updated_at: 300 },
    { workspace: "/a", updated_at: 200 },
    { workspace: "/b", updated_at: 100 },
  ];

  it("is the old order when nothing is pinned or stamped: newest conversation first", () => {
    expect(sidebarOrder(conversas, [{ path: "/c", alias: "" }], "")).toEqual(["/b", "/a", "/c"]);
  });

  it("puts pinned projects first, then the most recently used", () => {
    const rows = [{ path: "/c", alias: "", pinned: true }];
    expect(sidebarOrder(conversas, rows, "")).toEqual(["/c", "/b", "/a"]);
  });

  it("counts a turn that started after the newest saved conversation", () => {
    const rows = [{ path: "/a", alias: "", last_used_at: new Date(400_000).toISOString() }];
    expect(sidebarOrder(conversas, rows, "")).toEqual(["/a", "/b"]);
  });

  it("leaves a removed folder out, conversations and all, unless it is the one open", () => {
    const rows = [{ path: "/b", alias: "", hidden: true }];
    expect(sidebarOrder(conversas, rows, "")).toEqual(["/a"]);
    expect(sidebarOrder(conversas, rows, "/b")).toEqual(["/b", "/a"]);
  });
});
