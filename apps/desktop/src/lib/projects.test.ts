import { beforeEach, describe, expect, it } from "vitest";
import {
  ALIASES_KEY,
  PROJECTS_KEY,
  addProject,
  basename,
  projectLabel,
  readAliases,
  readProjects,
  removeProject,
  setAlias,
} from "@/lib/projects";

/**
 * The sidebar has always grouped by project, but a project could only appear there by having
 * already been talked about — so you could not add one before using it, and its name was whatever
 * the last path segment happened to be.
 */
describe("projects", () => {
  beforeEach(() => localStorage.clear());

  it("starts empty and adds in order", () => {
    expect(readProjects()).toEqual([]);
    addProject("/a");
    addProject("/b");
    expect(readProjects()).toEqual(["/a", "/b"]);
  });

  it("adding twice is not two projects", () => {
    addProject("/a");
    addProject("/a");
    expect(readProjects()).toEqual(["/a"]);
  });

  it("a blank path is not a project", () => {
    addProject("   ");
    expect(readProjects()).toEqual([]);
  });

  it("forgetting a project forgets its name too", () => {
    addProject("/a");
    setAlias("/a", "Chimera VPS");
    removeProject("/a");
    expect(readProjects()).toEqual([]);
    expect(readAliases()).toEqual({});
  });

  it("clearing a name removes it rather than storing an empty one", () => {
    // "No alias" has to round-trip as absence: an empty string reads identically to a missing key
    // but is a different statement, and the fallback name depends on telling them apart.
    setAlias("/a", "Something");
    setAlias("/a", "  ");
    expect(readAliases()).toEqual({});
    expect(projectLabel("/a", readAliases())).toBe("a");
  });

  it("survives storage holding something that is not a project list", () => {
    // A preference is never worth throwing over — the same discipline theme.ts and workspace.ts
    // follow. A corrupt key reads as "nothing configured", not as a broken screen.
    localStorage.setItem(PROJECTS_KEY, "{ not json");
    localStorage.setItem(ALIASES_KEY, "[1,2,3]");
    expect(readProjects()).toEqual([]);
    expect(readAliases()).toEqual({});
  });

  it("names a project the way you named it, and the folder otherwise", () => {
    const aliases = setAlias("/home/me/code/prova-analytics-saas", "PassaPro");
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
