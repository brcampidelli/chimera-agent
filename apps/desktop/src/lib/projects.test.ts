import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ALIASES_KEY,
  MIGRATED_KEY,
  PROJECTS_KEY,
  aliasesOf,
  basename,
  legacyAliases,
  legacyProjects,
  loadProjects,
  projectLabel,
} from "@/lib/projects";

const listCodeProjects = vi.fn();
const registerCodeProject = vi.fn();

vi.mock("@/lib/api", () => ({
  listCodeProjects: (...args: unknown[]) => listCodeProjects(...args),
  registerCodeProject: (...args: unknown[]) => registerCodeProject(...args),
}));

/**
 * The list used to live here, in one browser profile. Moving it to the server is only safe if a
 * running install carries its projects across on the first load after the update — so most of this
 * file is about the migration, and specifically about the two ways it can lie: running twice, and
 * claiming to be done when it was not.
 */
describe("projects", () => {
  beforeEach(() => {
    localStorage.clear();
    listCodeProjects.mockReset().mockResolvedValue([]);
    registerCodeProject.mockReset().mockResolvedValue([]);
  });

  it("asks the server, not this browser", async () => {
    listCodeProjects.mockResolvedValue([{ path: "/a", alias: "A" }]);
    await expect(loadProjects()).resolves.toEqual([{ path: "/a", alias: "A" }]);
  });

  it("hands the old list to the server once, names and all", async () => {
    localStorage.setItem(PROJECTS_KEY, JSON.stringify(["/a", "/b"]));
    localStorage.setItem(ALIASES_KEY, JSON.stringify({ "/a": "Chimera VPS" }));

    await loadProjects();

    expect(registerCodeProject.mock.calls).toEqual([
      ["/a", "Chimera VPS"],
      // Undefined, never "": an empty alias CLEARS a name, so migrating an unnamed project with one
      // would erase a name the server might already hold.
      ["/b", undefined],
    ]);
  });

  it("does not migrate again on the next load", async () => {
    localStorage.setItem(PROJECTS_KEY, JSON.stringify(["/a"]));
    await loadProjects();
    await loadProjects();
    expect(registerCodeProject).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem(MIGRATED_KEY)).toBe("1");
  });

  it("a fresh install is marked migrated without a single request", async () => {
    await loadProjects();
    expect(registerCodeProject).not.toHaveBeenCalled();
    expect(localStorage.getItem(MIGRATED_KEY)).toBe("1");
  });

  it("a migration that fails is retried rather than recorded as done", async () => {
    // The failure mode that would silently lose somebody's list: mark it migrated, then discover the
    // backend was not up yet. Re-registering is idempotent on the path, so retrying is free.
    localStorage.setItem(PROJECTS_KEY, JSON.stringify(["/a"]));
    registerCodeProject.mockRejectedValueOnce(new Error("backend not up"));

    await expect(loadProjects()).rejects.toThrow("backend not up");
    expect(localStorage.getItem(MIGRATED_KEY)).toBeNull();

    await loadProjects();
    expect(localStorage.getItem(MIGRATED_KEY)).toBe("1");
  });

  it("reads an old list that is not a list as no list", () => {
    localStorage.setItem(PROJECTS_KEY, "{ not json");
    localStorage.setItem(ALIASES_KEY, "[1,2,3]");
    expect(legacyProjects()).toEqual([]);
    expect(legacyAliases()).toEqual({});
  });

  it("keeps the old keys after migrating", async () => {
    // Nothing here is worth destroying to save two keys, and an older build still reads them.
    localStorage.setItem(PROJECTS_KEY, JSON.stringify(["/a"]));
    await loadProjects();
    expect(legacyProjects()).toEqual(["/a"]);
  });

  it("turns the server's rows into the names the sidebar wants", () => {
    const aliases = aliasesOf([
      { path: "/a", alias: "PassaPro" },
      // An unnamed project must be ABSENT rather than empty: the fallback to the folder name is
      // what `projectLabel` decides on, and "" is a name that reads as no name and is not one.
      { path: "/b", alias: "" },
    ]);
    expect(aliases).toEqual({ "/a": "PassaPro" });
    expect(projectLabel("/b", aliases)).toBe("b");
  });

  it("names a project the way you named it, and the folder otherwise", () => {
    const aliases = { "/home/me/code/prova-analytics-saas": "PassaPro" };
    expect(projectLabel("/home/me/code/prova-analytics-saas", aliases)).toBe("PassaPro");
    expect(projectLabel("/home/me/code/other", aliases)).toBe("other");
  });

  it("finds the folder name on both platforms", () => {
    // A Windows user and a WSL checkout end up in the same list.
    expect(basename("C:\\Users\\me\\chimera-agent")).toBe("chimera-agent");
    expect(basename("/home/me/chimera-agent")).toBe("chimera-agent");
    // Trailing separators must not make one project read as two.
    expect(basename("/home/me/chimera-agent/")).toBe("chimera-agent");
    expect(basename("C:\\repo\\")).toBe("repo");
  });

  it("never returns an empty label", () => {
    // A path that is nothing but separators has no folder name, and a blank row in the sidebar is
    // unclickable in a way nobody can diagnose.
    expect(basename("/")).toBe("/");
    expect(projectLabel("", {})).toBe("");
  });
});
