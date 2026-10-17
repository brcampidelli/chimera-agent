import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar, sidebarSessionOrder } from "@/components/code/SessionSidebar";
import { listCodeProjects, listCodeSessions } from "@/lib/api";
import { COLLAPSED_KEY, GROUP_WORKTREES_KEY } from "@/lib/sidebar-prefs";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getApprovals: vi.fn(async () => []),
  listCodeSessions: vi.fn(),
  listRunningTurns: vi.fn(async () => []),
  listCodeProjects: vi.fn(async () => []),
  listArchivedCodeSessions: vi.fn(async () => []),
  registerCodeProject: vi.fn(),
  flagCodeProject: vi.fn(),
  forgetCodeProject: vi.fn(),
  deleteCodeProject: vi.fn(),
  deleteCodeSession: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
}));

const REPO = "C:\\Projetos\\Agent AI";

function worktree(i: number, over: Record<string, unknown> = {}) {
  return {
    id: `w${i}`,
    title: `Tarefa ${i}`,
    workspace: `C:\\Temp\\wt\\w-${i}`,
    turns: 1,
    updated_at: 1000 - i,
    project: REPO,
    project_name: "Agent AI",
    checkout_kind: "worktree",
    branch: `feat/task-${i}`,
    ...over,
  };
}

const TWENTY_TWO = Array.from({ length: 22 }, (_, i) => worktree(i));

function render(props: Partial<Parameters<typeof SessionSidebar>[0]> = {}) {
  const onNewIn = vi.fn();
  renderWithProviders(
    <SessionSidebar
      workspace=""
      activeSession={null}
      onResume={vi.fn()}
      onNew={vi.fn()}
      onNewIn={onNewIn}
      onProject={vi.fn()}
      {...props}
    />,
  );
  return { onNewIn };
}

/** The section headers on screen, by the folder each carries as its title. */
function headers(): HTMLElement[] {
  return screen.getAllByRole("button").filter((b) => b.hasAttribute("aria-expanded") && b.title !== "" && b.title !== "List options");
}

/** The conversation rows on screen, top to bottom, by title. */
function rowTitles(): string[] {
  return screen
    .getAllByRole("button")
    .filter((b) => b.title.startsWith("Tarefa") || b.title.startsWith("Outro"))
    .map((b) => b.title);
}

/**
 * A3 and A4 of study 03. The owner's sidebar listed 22 worktrees of one repository as 22 top-level
 * sections with the same truncated title. A1 (#965) stamped each conversation with its repository;
 * this is the list using it — and taking the shape of the Claude desktop app's project list.
 */
describe("SessionSidebar — sections by project", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue(TWENTY_TWO as never);
    vi.mocked(listCodeProjects).mockResolvedValue([] as never);
  });

  it("lists 22 worktrees of one repository as ONE section with a worktree count", async () => {
    render();

    await screen.findByText("Agent AI");
    expect(headers().map((h) => h.title)).toEqual([REPO]);
    expect(screen.getByText("22 worktree(s)")).toBeInTheDocument();
    // Five rows, then the way to the rest — not 22 rows pushing every other project off screen.
    expect(rowTitles()).toHaveLength(5);
    await userEvent.click(screen.getByRole("button", { name: "Show 17 more" }));
    expect(rowTitles()).toHaveLength(22);
    await userEvent.click(screen.getByRole("button", { name: "Show less" }));
    expect(rowTitles()).toHaveLength(5);
  });

  it("shows a worktree's branch on the row's second line, and no branch for the main checkout", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      worktree(1),
      worktree(2, { title: "Tarefa main", workspace: REPO, checkout_kind: "main", branch: "main" }),
    ] as never);
    render();

    const row = await screen.findByTitle("Tarefa 1");
    expect(within(row).getByText("feat/task-1")).toBeInTheDocument();
    expect(within(screen.getByTitle("Tarefa main")).queryByText("main")).toBeNull();
  });

  it("turning grouping off brings back one section per folder, and remembers it", async () => {
    const user = userEvent.setup();
    vi.mocked(listCodeSessions).mockResolvedValue(TWENTY_TWO.slice(0, 3) as never);
    render();

    await screen.findByText("Agent AI");
    expect(headers()).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "List options" }));
    const toggle = await screen.findByRole("menuitemcheckbox", { name: "Group worktrees under their project" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);

    await waitFor(() => expect(headers()).toHaveLength(3));
    expect(headers().map((h) => h.title)).toEqual(["C:\\Temp\\wt\\w-0", "C:\\Temp\\wt\\w-1", "C:\\Temp\\wt\\w-2"]);
    expect(screen.queryByText(/worktree\(s\)/)).toBeNull();
    expect(localStorage.getItem(GROUP_WORKTREES_KEY)).toBe("0");
  });

  it("walks Ctrl+Tab in exactly the order the rows are drawn, with grouping on and off", async () => {
    const sessions = [
      worktree(1),
      { id: "o", title: "Outro projeto", workspace: "/b/blog", turns: 1, updated_at: 999, project: "/b/blog", project_name: "blog", checkout_kind: "main" },
      worktree(2),
      worktree(3),
    ];
    vi.mocked(listCodeSessions).mockResolvedValue(sessions as never);
    render();

    await screen.findByText("Agent AI");
    const walk = () => sidebarSessionOrder(sessions as never, [], "").map((s) => s.title);
    expect(walk()).toEqual(rowTitles());
    expect(walk()).toEqual(["Tarefa 1", "Tarefa 2", "Tarefa 3", "Outro projeto"]);

    await userEvent.click(screen.getByRole("button", { name: "List options" }));
    await userEvent.click(await screen.findByRole("menuitemcheckbox", { name: /Group worktrees/ }));
    await waitFor(() => expect(headers()).toHaveLength(4));
    expect(walk()).toEqual(rowTitles());
    expect(walk()).toEqual(["Tarefa 1", "Outro projeto", "Tarefa 2", "Tarefa 3"]);
  });

  it("names two different repositories with the same folder name by their parent folder", async () => {
    vi.mocked(listCodeSessions).mockResolvedValue([
      worktree(1, { workspace: "/home/me/a/app", project: "/home/me/a/app", project_name: "app", checkout_kind: "main" }),
      worktree(2, { workspace: "/home/me/b/app", project: "/home/me/b/app", project_name: "app", checkout_kind: "main" }),
    ] as never);
    render();

    await waitFor(() => expect(headers()).toHaveLength(2));
    expect(within(headers()[0]).getByText("a")).toBeInTheDocument();
    expect(within(headers()[1]).getByText("b")).toBeInTheDocument();
  });

  it("keeps a hidden project hidden together with its worktrees", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([{ path: REPO, alias: "", hidden: true }] as never);
    vi.mocked(listCodeSessions).mockResolvedValue([
      ...TWENTY_TWO.slice(0, 3),
      { id: "o", title: "Outro projeto", workspace: "/b/blog", turns: 1, updated_at: 1 },
    ] as never);
    render();

    await screen.findByTitle("Outro projeto");
    expect(screen.queryByText("Agent AI")).toBeNull();
    expect(rowTitles()).toEqual(["Outro projeto"]);
  });

  it("'+' starts a conversation in the project's main checkout, not in a worktree", async () => {
    const { onNewIn } = render();

    await userEvent.click(await screen.findByRole("button", { name: "New conversation in Agent AI" }));
    expect(onNewIn).toHaveBeenCalledWith(REPO);
  });

  it("folds a section from the keyboard, keeps the focus on it, and remembers the fold", async () => {
    const user = userEvent.setup();
    vi.mocked(listCodeSessions).mockResolvedValue(TWENTY_TWO.slice(0, 2) as never);
    render();

    await screen.findByTitle("Tarefa 0");
    const header = headers()[0];
    header.focus();
    await user.keyboard("{Enter}");
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(rowTitles()).toEqual([]);
    expect(header).toHaveFocus();
    expect(JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? "[]")).toEqual([REPO]);

    await user.keyboard(" ");
    expect(header).toHaveAttribute("aria-expanded", "true");
    expect(rowTitles()).toEqual(["Tarefa 0", "Tarefa 1"]);
    expect(header).toHaveFocus();
  });

  it("keeps the open conversation drawn inside a folded section and past the fifth row", async () => {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([REPO]));
    render({ activeSession: "w9" });

    await screen.findByText("Agent AI");
    expect(rowTitles()).toEqual(["Tarefa 9"]);
    await userEvent.click(headers()[0]);
    expect(rowTitles()).toEqual(["Tarefa 0", "Tarefa 1", "Tarefa 2", "Tarefa 3", "Tarefa 4", "Tarefa 9"]);
  });

  it("searches titles, branches and project names, and says when nothing matches", async () => {
    const user = userEvent.setup();
    render();

    await screen.findByText("Agent AI");
    const search = screen.getByRole("searchbox", { name: "Search conversations" });
    await user.type(search, "task-17");
    expect(rowTitles()).toEqual(["Tarefa 17"]);
    await user.clear(search);
    await user.type(search, "agent ai");
    expect(rowTitles()).toHaveLength(22); // every match, no "show more" while searching
    await user.clear(search);
    await user.type(search, "lefran");
    expect(screen.getByText("No conversation matches “lefran”.")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(search).toHaveValue("");
  });

  it("offers a contributed project action only when a feature contributes it", async () => {
    const onClean = vi.fn();
    const user = userEvent.setup();
    render({ projectActions: () => [{ id: "clean", label: "Clean finished worktrees", onSelect: onClean }] });

    await user.click(await screen.findByRole("button", { name: "More for Agent AI" }));
    await user.click(await screen.findByRole("menuitem", { name: "Clean finished worktrees" }));
    expect(onClean).toHaveBeenCalled();
  });

  it("has no dead entry for a feature that is not built", async () => {
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("button", { name: "More for Agent AI" }));
    await screen.findByRole("menuitem", { name: "Open the project" });
    expect(screen.getAllByRole("menuitem").map((m) => m.textContent)).toEqual([
      "Open the project",
      "Collapse",
      "Rename Agent AI",
      "Hide from the list",
      "Delete the project Agent AI",
    ]);
  });

  it("folds instantly: no exit animation to wait for, and only token timing on the chevron", async () => {
    // Reduced motion collapses durations to 1ms globally (index.css). That only holds if the fold
    // uses a token transition on transform and never animates height or keeps rows mounted to
    // animate them out — so the rows must be gone in the same act as the click.
    vi.mocked(listCodeSessions).mockResolvedValue(TWENTY_TWO.slice(0, 2) as never);
    document.documentElement.dataset.motion = "reduced";
    render();

    await screen.findByTitle("Tarefa 0");
    const header = headers()[0];
    const chevron = header.querySelector("svg") as SVGElement;
    const classes = chevron.getAttribute("class") ?? "";
    expect(classes).toContain("transition-transform");
    expect(classes).toContain("duration-1");
    expect(classes).toContain("ease-out");
    expect(classes).not.toMatch(/animate-|transition-\[height\]/);
    act(() => header.click());
    expect(screen.queryByTitle("Tarefa 0")).toBeNull();
    delete document.documentElement.dataset.motion;
  });
});
