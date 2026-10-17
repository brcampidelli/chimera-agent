import { describe, expect, it } from "vitest";

import type { CodeProject } from "@/lib/api";
import { sidebarOrder } from "@/lib/projects";
import { groupSessions, matchesSearch, orderedSessions, type GroupableSession } from "@/lib/sidebar-groups";

type Row = GroupableSession & { id: string; title: string; branch?: string | null };

function worktree(i: number, over: Partial<Row> = {}): Row {
  return {
    id: `w${i}`,
    title: "Leia TAREFA.md com read_file e execute",
    workspace: `C:\\Temp\\wt\\w-${i}`,
    updated_at: 100 - i,
    project: "C:\\Projetos\\Agent AI",
    project_name: "Agent AI",
    checkout_kind: "worktree",
    branch: `feat/task-${i}`,
    ...over,
  };
}

const ON = { byProject: true };
const OFF = { byProject: false };

/** What A3 is for: the owner's disk had 22 checkouts of one repository as 22 top-level sections. */
describe("sidebar grouping — by repository", () => {
  it("files 22 worktrees and the main checkout of one repository under ONE section", () => {
    const sessions: Row[] = [
      ...Array.from({ length: 22 }, (_, i) => worktree(i)),
      { id: "m", title: "main", workspace: "C:\\Projetos\\Agent AI", updated_at: 1, project: "C:\\Projetos\\Agent AI", project_name: "Agent AI", checkout_kind: "main", branch: "main" },
    ];
    const { groups } = groupSessions(sessions, [], "", ON);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ key: "C:\\Projetos\\Agent AI", label: "Agent AI", worktrees: 22, hint: "" });
    expect(groups[0].sessions).toHaveLength(23);
  });

  it("with grouping off, gives each folder its section exactly as the old list did", () => {
    const sessions = Array.from({ length: 3 }, (_, i) => worktree(i));
    const rows: CodeProject[] = [{ path: "/c", alias: "" }];
    const { groups } = groupSessions(sessions, rows, "", OFF);
    // The old order function is the reference: switching grouping off must be the old list.
    expect(groups.map((g) => g.key)).toEqual(sidebarOrder(sessions, rows, ""));
    expect(groups.every((g) => g.worktrees === 0)).toBe(true);
  });

  it("keeps two clones of one remote apart — the server stamps different projects for them", () => {
    const a = worktree(1, { workspace: "/a/app", project: "/a/app", project_name: "app", checkout_kind: "main" });
    const b = worktree(2, { workspace: "/b/app", project: "/b/app", project_name: "app", checkout_kind: "main" });
    const { groups } = groupSessions([a, b], [], "", ON);
    expect(groups.map((g) => g.key)).toEqual(["/a/app", "/b/app"]);
    // Same name, so each says where it lives.
    expect(groups.map((g) => g.hint)).toEqual(["a", "b"]);
  });

  it("merges a folder named like an alias into the repository it is a worktree of", () => {
    // `Agent AI` is aliased `chimera-agent`, and `…\chimera-agent` is a worktree of the same repo:
    // two sections with one label before, one section now.
    const rows: CodeProject[] = [{ path: "C:\\Projetos\\Agent AI", alias: "chimera-agent" }];
    const s = worktree(1, { workspace: "C:\\Projetos\\chimera-agent" });
    const { groups } = groupSessions([s], rows, "", ON);
    expect(groups).toHaveLength(1);
    expect(groups[0]).toMatchObject({ key: "C:\\Projetos\\Agent AI", label: "chimera-agent", hint: "" });
  });

  it("files under the registered row even when the server spells the path differently", () => {
    const rows: CodeProject[] = [{ path: "c:/projetos/agent ai/", alias: "Agent", pinned: true }];
    const { groups } = groupSessions([worktree(1)], rows, "", ON);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe("c:/projetos/agent ai/");
    expect(groups[0].label).toBe("Agent");
  });

  it("folds a registered worktree row into its project instead of an empty section of its own", () => {
    const rows: CodeProject[] = [{ path: "C:\\Temp\\wt\\w-1", alias: "" }];
    const { groups } = groupSessions([worktree(1)], rows, "", ON);
    expect(groups.map((g) => g.key)).toEqual(["C:\\Projetos\\Agent AI"]);
  });

  it("hides a hidden project's worktrees with it — unless one of them is open", () => {
    const rows: CodeProject[] = [{ path: "C:\\Projetos\\Agent AI", alias: "", hidden: true }];
    const sessions = [worktree(1), worktree(2), worktree(3, { workspace: "/x", project: "/x", project_name: "x" })];
    expect(groupSessions(sessions, rows, "", ON).groups.map((g) => g.key)).toEqual(["/x"]);
    const open = groupSessions(sessions, rows, "C:\\Temp\\wt\\w-2", ON);
    expect(open.currentKey).toBe("C:\\Projetos\\Agent AI");
    expect(open.groups.map((g) => g.key)).toContain("C:\\Projetos\\Agent AI");
  });

  it("groups an unstamped conversation by its folder, as before", () => {
    const old = { id: "o", title: "old", workspace: "/gone", updated_at: 5 };
    expect(groupSessions([old], [], "", ON).groups.map((g) => g.key)).toEqual(["/gone"]);
  });

  it("walks Ctrl+Tab in the order the sections list", () => {
    const sessions = [worktree(1), worktree(2, { project: "/y", project_name: "y", workspace: "/y" }), worktree(3)];
    const order = orderedSessions(sessions, [], "", ON).map((s) => s.id);
    expect(order).toEqual(["w1", "w3", "w2"]);
    expect(orderedSessions(sessions, [], "", OFF).map((s) => s.id)).toEqual(["w1", "w2", "w3"]);
  });
});

describe("sidebar search", () => {
  it("matches the title, the branch or the section's name, ignoring case", () => {
    const s = { title: "Abas do painel", branch: "feat/panel-tabs" };
    expect(matchesSearch(s, "Agent AI", "PAINEL")).toBe(true);
    expect(matchesSearch(s, "Agent AI", "panel-tabs")).toBe(true);
    expect(matchesSearch(s, "Agent AI", "agent")).toBe(true);
    expect(matchesSearch(s, "Agent AI", "lefran")).toBe(false);
    expect(matchesSearch(s, "Agent AI", "  ")).toBe(true);
  });
});
