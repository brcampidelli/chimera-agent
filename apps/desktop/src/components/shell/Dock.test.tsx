import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Activity } from "@/components/Activity";
import { ComposerSettings } from "@/components/code/ComposerSettings";
import { AppShell } from "@/components/shell/AppShell";
import { announcements, dropTarget } from "@/components/shell/Dock";
import { DOCK_PANELS, useSilentPanels } from "@/components/shell/panels";
import { ToastProvider } from "@/components/ui/toast";
import { AgentProvider, useAgent, type AgentState } from "@/lib/agent-context";
import type { ConversationSide } from "@/lib/conversation-side";
import { useT } from "@/lib/i18n";
import { DOCK_PANEL_IDS, applyLayout, defaultLayout } from "@/lib/layout/model";
import { getGitStatus, getMcpServers, getResources, listJobs } from "@/lib/api";
import { STORAGE_KEY } from "@/lib/layout/store";
import { AGENT_AFTER_A_TURN, withoutDefaultTabs } from "@/test/agent";
import { gitStatus } from "@/test/code-api-mock";
import { PANEL_API, WithConversation, settled as settleReads } from "@/test/panels";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));
// Every server-backed panel has something to show unless a test says otherwise (`useEmptyServer`).
vi.mock("@/lib/api", async (importOriginal) => {
  const { PANEL_API: api } = await import("@/test/panels");
  return {
    ...(await importOriginal<typeof import("@/lib/api")>()),
    getApprovals: vi.fn(api.getApprovals),
    getResources: vi.fn(api.getResources),
    listJobs: vi.fn(api.listJobs),
    getMcpServers: vi.fn(api.getMcpServers),
    getGitStatus: vi.fn(api.getGitStatus),
  };
});

beforeEach(() => {
  vi.mocked(getResources).mockImplementation(PANEL_API.getResources as never);
  vi.mocked(listJobs).mockImplementation(PANEL_API.listJobs as never);
  vi.mocked(getMcpServers).mockImplementation(PANEL_API.getMcpServers as never);
  vi.mocked(getGitStatus).mockImplementation(PANEL_API.getGitStatus as never);
});

/** The server with nothing for any panel: no job, no machine reading, no MCP server read yet, no git
 *  status yet. The reads that never answer are the panels' loading state, which draws nothing. */
function useEmptyServer() {
  vi.mocked(listJobs).mockResolvedValue({ jobs: [] });
  vi.mocked(getResources).mockRejectedValue(new Error("no reading in this test"));
  vi.mocked(getMcpServers).mockImplementation(() => new Promise(() => {}));
  vi.mocked(getGitStatus).mockImplementation(() => new Promise(() => {}));
}

/**
 * Phase 4 of the dynamic screen: the right panel's sections are panels that can be minimised, closed,
 * reordered and moved between the right panel, the left sidebar and the bottom dock. The agent's state
 * line is not one of them, and stays put.
 */
let publish: AgentState["publish"] | null = null;

function Shell() {
  publish = useAgent().publish;
  return (
    <AppShell viewKey="code" viewLabel="Code" rail={<nav aria-label="rail" />} inspector={<Activity />}>
      <p>conversation</p>
    </AppShell>
  );
}

/** Mounted after one turn and with a conversation on screen unless told otherwise: a panel with
 *  nothing to show is not drawn, and most of what is tested here is what a panel on screen does. */
function mount(
  agent: Partial<Omit<AgentState, "publish">> = AGENT_AFTER_A_TURN,
  { conversation = true, side }: { conversation?: boolean; side?: Partial<ConversationSide> } = {},
) {
  const shell = (
    <AgentProvider value={agent}>
      <Shell />
    </AgentProvider>
  );
  return renderWithProviders(
    <ToastProvider>{conversation ? <WithConversation value={side}>{shell}</WithConversation> : shell}</ToastProvider>,
  );
}

function panelNames(region: HTMLElement): string[] {
  return Array.from(region.querySelectorAll("section[data-panel]")).map((s) => s.getAttribute("aria-label") ?? "");
}

function panelIds(region: HTMLElement): string[] {
  return Array.from(region.querySelectorAll("section[data-panel]")).map((s) => s.getAttribute("data-panel") ?? "");
}

describe("the docks", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem("chimera.lang", "en");
    publish = null;
  });

  it("draws the right panel's sections as panels, most urgent first, below a state line that is not one", async () => {
    mount();
    const right = screen.getByRole("region", { name: "Right panel" });

    // What runs in the background first, where the conversation's task list was before the two were
    // merged; the last turn's three cards as one frame of tabs (drawn as its selected tab, Tokens);
    // the machine at the end (ESTUDO-03 §4.3). Every panel has something to show here.
    await waitFor(() =>
      expect(panelNames(right)).toEqual([
        "Changes", "Background jobs", "Tools", "Context", "Connectors", "Tokens", "This machine", "Environment",
      ]),
    );
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Tokens", "Memory", "Fusion"]);
    // The state line: no drag handle, no move, no close.
    expect(right.parentElement).toHaveTextContent("idle");
    expect(screen.queryByRole("button", { name: /Status/ })).not.toBeInTheDocument();
  });

  it("does not repeat the completed state in the panel above the status bar", () => {
    mount({ ...AGENT_AFTER_A_TURN, status: "done" });
    const right = screen.getByRole("region", { name: "Right panel" });
    expect(right).not.toHaveTextContent("done");
    expect(right.parentElement).toHaveTextContent("done");
  });

  it("renders a human memory-recall label with the retrieval layer available on hover", () => {
    localStorage.setItem("chimera.lang", "en");
    mount({ ...AGENT_AFTER_A_TURN, report: { prompt_tokens: 100, completion_tokens: 25, usd: 0, memory_facts_used: 1, memory_layer: "fts" } });
    const right = screen.getByRole("region", { name: "Right panel" });
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    const memory = right.querySelector('[data-panel="activity.memory"]')!;
    expect(memory).toHaveTextContent("1 memory used this turn");
    expect(memory).not.toHaveTextContent("(fts)");
    expect(memory.querySelector("span[title]")).toHaveAttribute("title", "Memory layer: fts");
  });

  it("moves a panel to the bottom dock from its menu, and the dock appears only then", async () => {
    const user = userEvent.setup();
    mount();
    expect(screen.queryByRole("region", { name: "Bottom dock" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Move Tools" }));
    await user.click(await screen.findByRole("menuitem", { name: "To the bottom dock" }));

    const bottom = await screen.findByRole("region", { name: "Bottom dock" });
    expect(panelNames(bottom)).toEqual(["Tools"]);
    expect(panelNames(screen.getByRole("region", { name: "Right panel" }))).not.toContain("Tools");
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}").panels["activity.tools"].zone).toBe("bottom");
  });

  it("minimises a panel to its title and expands it again", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByRole("button", { name: "Minimize Tools" }));
    const tools = screen.getByRole("region", { name: "Tools" });
    expect(tools.querySelector("[data-panel-body]")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Expand Tools" }));
    expect(screen.getByRole("region", { name: "Tools" }).querySelector("[data-panel-body]")).not.toBeNull();
  });

  it("closes a panel with an Undo, and lists it in the hidden-panels tray meanwhile", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByRole("button", { name: "Close Tokens" }));
    expect(screen.queryByRole("region", { name: "Tokens" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hidden: 1" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Undo" }));
    await waitFor(() => expect(screen.getByRole("region", { name: "Tokens" })).toBeInTheDocument());
  });

  // Until 0.69.1 this asserted that the empty Background jobs frame was in the DOM with a `:has()` class
  // that hides it. jsdom applies no CSS, so the test passed on a frame the class could not reach when
  // minimised or tabbed, which is what the owner saw. The rule is now that the frame is not drawn.
  it("leaves out a panel whose content has nothing to say, frame included, rather than an empty box", async () => {
    vi.mocked(listJobs).mockResolvedValue({ jobs: [] });
    mount();
    await waitFor(() => expect(listJobs).toHaveBeenCalled());
    await screen.findByRole("region", { name: "Changes" });
    expect(screen.queryByRole("region", { name: "Background jobs" })).not.toBeInTheDocument();
  });
});

describe("a panel with nothing to say", () => {
  beforeEach(() => {
    localStorage.clear();
    publish = null;
  });

  const SILENT = ["activity.tools", "activity.tokens", "activity.memory", "activity.fusion"];

  it("is not drawn before the first turn, frame included, and comes back with its first data", () => {
    mount({});
    const right = () => screen.getByRole("region", { name: "Right panel" });
    const drawn = (id: string) => document.querySelector(`[data-panel="${id}"]`);
    // Not a "—", not "No tools": no frame at all, so no title or buttons either. The last turn's tab
    // group has no member with anything to say, so the whole group is gone with them.
    for (const id of SILENT) expect(drawn(id)).toBeNull();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Move Tools" })).not.toBeInTheDocument();
    expect(screen.queryByText("—")).not.toBeInTheDocument();
    expect(screen.queryByText("No tools")).not.toBeInTheDocument();

    act(() => publish?.({ tools: [{ name: "read_file", ok: true }] }));
    expect(panelIds(right())).toContain("activity.tools");
    expect(screen.getByRole("region", { name: "Tools" })).toHaveTextContent("read_file");
    // Only the tools reported: tokens, memory and fusion still have nothing to say.
    for (const id of SILENT.slice(1)) expect(right().querySelector(`[data-panel="${id}"]`)).toBeNull();

    act(() => publish?.({ report: AGENT_AFTER_A_TURN.report }));
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Tokens", "Memory", "Fusion"]);

    // A new conversation clears the turn: the frames go again.
    act(() => publish?.({ tools: [], report: null }));
    for (const id of SILENT) expect(drawn(id)).toBeNull();
  });

  it("drops out of its tab strip while its tabmates still draw, and never counts an absent number as zero", () => {
    // Tokens were reported, but no recall count and no fusion: the strip shrinks to the one tab with
    // something to say, which draws as a lone panel rather than a strip of one.
    mount({ report: { prompt_tokens: 10, completion_tokens: 2, usd: null } });
    const right = screen.getByRole("region", { name: "Right panel" });
    expect(right.querySelector('[data-panel="activity.tokens"]')).not.toBeNull();
    expect(right.querySelector('[data-panel="activity.memory"]')).toBeNull();
    expect(right.querySelector('[data-panel="activity.fusion"]')).toBeNull();
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByText(/facts recalled/)).not.toBeInTheDocument();
  });

  it("stays out when minimised, and is not offered in the Group with menu", async () => {
    const user = userEvent.setup();
    withoutDefaultTabs();
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    stored.panels["activity.memory"].mode = "minimized";
    localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
    mount({ tools: [{ name: "read_file", ok: true }] });

    const right = screen.getByRole("region", { name: "Right panel" });
    expect(right.querySelector('[data-panel="activity.memory"]')).toBeNull();
    await user.click(screen.getByRole("button", { name: "Move Tools" }));
    await screen.findByRole("menuitem", { name: "To the bottom dock" });
    expect(screen.queryByRole("menuitem", { name: /Group as tabs with (Tokens|Memory|Fusion)$/ })).not.toBeInTheDocument();
  });
});

describe("a panel's header", () => {
  beforeEach(() => localStorage.clear());

  it("names the panel in sentence case, with a short title where the full one does not fit", async () => {
    mount();
    await screen.findByRole("region", { name: "This machine" });
    await screen.findByRole("region", { name: "Background jobs" });
    for (const id of ["activity.machine", "activity.jobs", "activity.tools"]) {
      const section = document.querySelector(`section[data-panel="${id}"]`) as HTMLElement;
      const title = section.querySelector("span[title]") as HTMLElement;
      expect(title.className).not.toMatch(/uppercase|tracking-/);
      // The full name stays the panel's accessible name and its pointer tooltip.
      expect(title.getAttribute("title")).toBe(section.getAttribute("aria-label"));
    }
    const shown = (id: string) => (document.querySelector(`section[data-panel="${id}"] span[title]`) as HTMLElement).textContent;
    expect(shown("activity.machine")).toBe("Machine");
    expect(shown("activity.jobs")).toBe("Jobs");
    expect(shown("activity.tools")).toBe("Tools");
    // The tabs drop the capitals too.
    for (const tab of screen.getAllByRole("tab")) expect(tab.className).not.toMatch(/uppercase|tracking-/);
  });

  it("keeps drag and close hidden until hover or keyboard focus, and shows move, maximise and minimise on hover or focus", () => {
    mount();
    const tools = screen.getByRole("region", { name: "Tools" });
    const controls = tools.querySelector("[data-panel-controls]") as HTMLElement;
    expect(within(controls).getByRole("button", { name: "Move Tools" })).toBeInTheDocument();
    const drag = within(tools).getByRole("button", { name: "Drag Tools" });
    const close = within(tools).getByRole("button", { name: "Close Tools" });
    expect(drag.className).toContain("hidden");
    expect(drag.className).toContain("group-hover/panel:inline-flex");
    expect(drag.className).toContain("group-focus-within/panel:inline-flex");
    expect(close.className).toContain("hidden");
    expect(close.className).toContain("group-hover/panel:inline-flex");
    expect(close.className).toContain("group-focus-within/panel:inline-flex");
    expect(within(controls).getByRole("button", { name: "Maximize Tools" })).toBeInTheDocument();
    expect(within(controls).getByRole("button", { name: "Minimize Tools" })).toBeInTheDocument();
    // Taking no room when hidden is what gives the title its width back: display, not opacity.
    expect(controls.className).toMatch(/(^| )hidden( |$)/);
    expect(controls.className).toContain("group-hover/panel:flex");
    expect(controls.className).toContain("group-focus-within/panel:flex");
    expect(tools.className).toContain("group/panel");
    expect(within(controls).queryByRole("button", { name: "Drag Tools" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Close Tools" })).not.toBeInTheDocument();
  });

  it("reaches every hidden control by keyboard, in order, from the drag handle", async () => {
    const user = userEvent.setup();
    mount();
    const tools = screen.getByRole("region", { name: "Tools" });
    within(tools).getByRole("button", { name: "Drag Tools" }).focus();
    // Focus inside the panel is what shows the hidden three (`group-focus-within`), so Tab walks them.
    expect(tools.matches(":focus-within")).toBe(true);
    await user.tab();
    expect(within(tools).getByRole("button", { name: "Move Tools" })).toHaveFocus();
    await user.tab();
    expect(within(tools).getByRole("button", { name: "Maximize Tools" })).toHaveFocus();
    await user.tab();
    expect(within(tools).getByRole("button", { name: "Minimize Tools" })).toHaveFocus();
    await user.tab();
    expect(within(tools).getByRole("button", { name: "Close Tools" })).toHaveFocus();
    // And back: the close button remains keyboard reachable as focus moves through the panel.
    await user.tab({ shift: true });
    expect(within(tools).getByRole("button", { name: "Minimize Tools" })).toHaveFocus();
  });

  it("keeps the controls shown while the move menu is open, so the menu keeps its anchor", async () => {
    const user = userEvent.setup();
    mount();
    const trigger = screen.getByRole("button", { name: "Move Tools" });
    expect(trigger).not.toHaveAttribute("data-menu-open");
    await user.click(trigger);
    await screen.findByRole("menuitem", { name: "To the bottom dock" });
    expect(trigger).toHaveAttribute("data-menu-open");
    expect(trigger.closest("[data-panel-controls]")?.className).toContain("has-[[data-menu-open]]:flex");
    await user.keyboard("{Escape}");
    expect(trigger).not.toHaveAttribute("data-menu-open");
  });

  it("shows and hides its controls at once, with nothing for reduced motion to take away", () => {
    document.documentElement.dataset.motion = "reduced";
    try {
      mount();
      const tools = screen.getByRole("region", { name: "Tools" });
      const controls = tools.querySelector("[data-panel-controls]") as HTMLElement;
      const title = tools.querySelector("span[title]") as HTMLElement;
      for (const el of [controls, title]) expect(el.className).not.toMatch(/transition|animate-|duration-/);
    } finally {
      delete document.documentElement.dataset.motion;
    }
  });
});

describe("where a drop lands", () => {
  it("lands before the panel it is dropped on, and at the end of a zone dropped on directly", () => {
    const layout = defaultLayout();
    expect(dropTarget(layout, "activity.memory")).toEqual({ zone: "right", index: 6 });
    expect(dropTarget(layout, "zone:right")).toEqual({ zone: "right", index: 10 });
    expect(dropTarget(layout, "zone:bottom")).toEqual({ zone: "bottom", index: 0 });
  });

  it("refuses somewhere that is not a dock", () => {
    const layout = defaultLayout();
    expect(dropTarget(layout, "viewer")).toBeNull();
    expect(dropTarget(layout, "composer.config")).toBeNull();
  });

  it("names the panel and the zone at every step, for a screen reader", () => {
    let said: ReturnType<typeof announcements> | null = null;
    function Probe() {
      said = announcements(useT(), applyLayout(defaultLayout(), { type: "move", panel: "activity.jobs", zone: "left", index: 0 }));
      return null;
    }
    renderWithProviders(<Probe />);
    const a = said as unknown as ReturnType<typeof announcements>;
    const active = { id: "activity.tools" } as never;

    expect(a.onDragStart({ active })).toBe("Picked up Tools.");
    expect(a.onDragOver?.({ active, over: { id: "activity.jobs" } as never })).toBe("Tools is over Left sidebar.");
    expect(a.onDragEnd({ active, over: { id: "zone:bottom" } as never })).toBe("Tools dropped in Bottom dock.");
    expect(a.onDragCancel({ active, over: null })).toBe("Moving Tools was cancelled.");
  });
});

describe("the composer's settings", () => {
  beforeEach(() => localStorage.clear());

  it("start as one line of chips, open with a click on that line, and minimise again", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ComposerSettings summary={["Chimera", "balanced", "gpt-6-luna"]}>
        <p>the pickers</p>
      </ComposerSettings>,
    );

    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
    expect(screen.getByTestId("composer-settings-line")).toHaveTextContent("Chimerabalancedgpt-6-luna");

    // The chips ARE the button: the whole line is the target, not a 14-pixel chevron at its end.
    const line = screen.getByRole("button", { name: "Expand Who runs, cost and model" });
    expect(line).toHaveTextContent("gpt-6-luna");
    await user.click(line);
    expect(screen.getByText("the pickers")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Minimize Who runs, cost and model" }));
    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
  });
});

describe("which panels are silent", () => {
  // The dock leaves a panel out on `useSilentPanels`; the panel itself returns null on its own test.
  // Two conditions written in two places drift apart, so they are checked against each other here on
  // every state that matters, the empty ones included.
  const STATES: [string, Partial<Omit<AgentState, "publish">>][] = [
    ["before any turn", {}],
    ["a tool, no report", { tools: [{ name: "read_file", ok: true }] }],
    ["tokens without a recall count", { report: { prompt_tokens: 1, completion_tokens: 1, usd: null } }],
    ["a recall count of zero", { report: { prompt_tokens: 1, completion_tokens: 1, usd: 0, memory_facts_used: 0 } }],
    ["a whole turn", AGENT_AFTER_A_TURN],
  ];
  const AGENT_PANELS = ["activity.tools", "activity.tokens", "activity.memory", "activity.fusion"] as const;

  it.each(STATES)("matches what each panel draws: %s", (_name, agent) => {
    let silent: ReadonlySet<string> = new Set();
    function Probe() {
      silent = useSilentPanels();
      return null;
    }
    for (const id of AGENT_PANELS) {
      const { container, unmount } = renderWithProviders(
        <AgentProvider value={agent}>
          <Probe />
          <div data-testid="body">{DOCK_PANELS[id]()}</div>
        </AgentProvider>,
      );
      const body = container.querySelector('[data-testid="body"]') as HTMLElement;
      expect({ id, empty: body.childElementCount === 0 }).toEqual({ id, empty: silent.has(id) });
      unmount();
    }
  });

  it("counts a recall of zero as something to say: zero facts is a measurement, absent is not", () => {
    let silent: ReadonlySet<string> = new Set();
    function Probe() {
      silent = useSilentPanels();
      return null;
    }
    renderWithProviders(
      <AgentProvider value={{ report: { prompt_tokens: 1, completion_tokens: 1, usd: 0, memory_facts_used: 0 } }}>
        <Probe />
      </AgentProvider>,
    );
    expect(silent.has("activity.memory")).toBe(false);
  });
});

/**
 * The regression guard the owner asked for after 0.69.0, whose right panel showed "Tools" and
 * "Background" as titles over nothing: every card in its empty state, in every way a stored layout can
 * hold it (open, minimised, a tab of a group), and not one frame may be drawn.
 *
 * "Empty" is each card's state with nothing to describe: no turn yet, no conversation on screen (any
 * screen but Code, or Code before it published), no background job, no machine reading, and the server
 * reads the conversation cards make still unanswered.
 */
describe("every card in its empty state", () => {
  beforeEach(() => {
    localStorage.clear();
    publish = null;
    useEmptyServer();
  });

  const DOCKED = DOCK_PANEL_IDS.filter((id) => id !== "agents.batch");

  function store(change: (stored: { panels: Record<string, { mode: string }>; tabGroups: string[][]; activeTabs: Record<string, string> }) => void) {
    const stored = JSON.parse(JSON.stringify(defaultLayout()));
    change(stored);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
  }

  async function settled() {
    await waitFor(() => expect(listJobs).toHaveBeenCalled());
    await waitFor(() => expect(getResources).toHaveBeenCalled());
    // Let the rejected and resolved reads land before looking.
    await act(async () => {});
  }

  function drawnPanels(): string[] {
    return Array.from(document.querySelectorAll("section[data-panel]")).map((s) => s.getAttribute("data-panel") ?? "");
  }

  it.each<[string, () => void]>([
    ["in the default layout", () => {}],
    ["every card minimised", () => store((s) => DOCKED.forEach((id) => (s.panels[id].mode = "minimized")))],
    ["every card a tab of one group", () =>
      store((s) => {
        s.tabGroups = [[...DOCKED]];
        s.activeTabs = { [DOCKED[0]]: DOCKED[0] };
      })],
    ["the 0.69 layout with Background jobs and Tools minimised and the old Tasks card", () =>
      store((s) => {
        s.panels["activity.jobs"].mode = "minimized";
        s.panels["activity.tools"].mode = "minimized";
        (s.panels as Record<string, unknown>)["conversation.tasks"] = { zone: "right", order: 0, mode: "minimized" };
      })],
  ])("draws no card %s", async (_name, arrange) => {
    arrange();
    mount({}, { conversation: false });
    await settled();
    expect(drawnPanels()).toEqual([]);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    // The docks themselves go when they hold nothing, so no empty column of frames is left either.
    expect(screen.queryByRole("region", { name: "Right panel" })).not.toBeInTheDocument();
  });

  // The owner's decision (2026-10-18): Context with no reading, Connectors with no server and Changes
  // with a clean folder are empty cards too. With a conversation on screen and nothing to report, the
  // only card left is Environment, whose folder and posture are always something to show.
  it.each<[string, () => void]>([
    ["in the default layout", () => {}],
    ["every card minimised", () => store((s) => DOCKED.forEach((id) => (s.panels[id].mode = "minimized")))],
    ["every card a tab of one group", () =>
      store((s) => {
        s.tabGroups = [[...DOCKED]];
        s.activeTabs = { [DOCKED[0]]: DOCKED[0] };
      })],
  ])("draws only Environment for a conversation with nothing to report, %s", async (_name, arrange) => {
    arrange();
    vi.mocked(getMcpServers).mockResolvedValue({ count: 0, servers: [] });
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ files: [] }));
    mount({}, { side: { workspace: "C:\\work\\shop", sessionId: null, live: null, peak: null } });
    await settled();
    await waitFor(() => expect(getGitStatus).toHaveBeenCalled());
    await waitFor(() => expect(getMcpServers).toHaveBeenCalled());
    await settled();
    expect(drawnPanels()).toEqual(["conversation.environment"]);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  });

  it("brings a minimised card back, as its header, with its first data", async () => {
    store((s) => {
      s.panels["activity.tools"].mode = "minimized";
      s.panels["activity.jobs"].mode = "minimized";
    });
    mount({}, { conversation: false });
    await settled();
    expect(drawnPanels()).toEqual([]);

    act(() => publish?.({ tools: [{ name: "read_file", ok: true }] }));
    const tools = screen.getByRole("region", { name: "Tools" });
    // Minimised with something to show is the person's choice, and stays a header.
    expect(tools.querySelector("[data-panel-body]")).toBeNull();
    expect(screen.queryByRole("region", { name: "Background jobs" })).not.toBeInTheDocument();
  });

  it("drops a silent tab out of its group, and draws a group with one member left as that panel", async () => {
    store((s) => {
      s.tabGroups = [["activity.jobs", "activity.tools", "activity.machine"]];
      s.activeTabs = { "activity.jobs": "activity.jobs" };
    });
    mount({ tools: [{ name: "read_file", ok: true }] }, { conversation: false });
    await settled();
    // Jobs (selected) and the machine have nothing: no strip of one, no empty selected tab.
    expect(drawnPanels()).toEqual(["activity.tools"]);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Tools" })).toHaveTextContent("read_file");
  });
});

describe("which panels are silent, against the server", () => {
  // The same check as above for the panels that read the server: the dock's reason to leave a panel out
  // and the panel's own decision to draw nothing come from one hook each, and agree in every state.
  const SERVER_STATES: [string, () => void, boolean][] = [
    ["nothing on the server, no conversation", useEmptyServer, false],
    ["nothing on the server, a conversation on screen", useEmptyServer, true],
    ["every read answered, a conversation on screen", () => {}, true],
    ["every read answered, no conversation", () => {}, false],
  ];

  it.each(SERVER_STATES)("matches what each panel draws: %s", async (_name, server, conversation) => {
    localStorage.clear();
    server();
    for (const id of DOCK_PANEL_IDS) {
      let silent: ReadonlySet<string> = new Set();
      function Probe() {
        silent = useSilentPanels();
        return null;
      }
      const tree = (
        <AgentProvider value={{}}>
          <Probe />
          <div data-testid="body">{DOCK_PANELS[id as keyof typeof DOCK_PANELS]()}</div>
        </AgentProvider>
      );
      const { container, unmount } = renderWithProviders(conversation ? <WithConversation>{tree}</WithConversation> : tree);
      // The reads resolve at once, but react-query delivers them on a timer: turns of the event loop,
      // not just microtasks, before comparing.
      await settleReads();
      const body = container.querySelector('[data-testid="body"]') as HTMLElement;
      expect({ id, empty: body.childElementCount === 0 }).toEqual({ id, empty: silent.has(id) });
      unmount();
    }
  });
});
