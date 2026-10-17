import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Activity } from "@/components/Activity";
import { ComposerSettings } from "@/components/code/ComposerSettings";
import { AppShell } from "@/components/shell/AppShell";
import { announcements, dropTarget } from "@/components/shell/Dock";
import { DOCK_PANELS, useSilentPanels } from "@/components/shell/panels";
import { ToastProvider } from "@/components/ui/toast";
import { AgentProvider, useAgent, type AgentState } from "@/lib/agent-context";
import { useT } from "@/lib/i18n";
import { applyLayout, defaultLayout } from "@/lib/layout/model";
import { STORAGE_KEY } from "@/lib/layout/store";
import { AGENT_AFTER_A_TURN, withoutDefaultTabs } from "@/test/agent";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getApprovals: vi.fn(async () => []),
  // The machine panel reads this; a rejection is a state it already draws ("unavailable").
  getResources: vi.fn(async () => {
    throw new Error("not in this test");
  }),
  listJobs: vi.fn(async () => ({ jobs: [] })),
}));

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

/** Mounted after one turn unless told otherwise: the tool, token, memory and fusion panels draw
 *  nothing before a turn, and most of what is tested here is what a panel on screen does. */
function mount(agent: Partial<Omit<AgentState, "publish">> = AGENT_AFTER_A_TURN) {
  return renderWithProviders(
    <ToastProvider>
      <AgentProvider value={agent}>
        <Shell />
      </AgentProvider>
    </ToastProvider>,
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
    publish = null;
  });

  it("draws the right panel's sections as panels, most urgent first, below a state line that is not one", () => {
    mount();
    const right = screen.getByRole("region", { name: "Right panel" });

    // The conversation's own work first; the last turn's three cards as one frame of tabs (drawn as its
    // selected tab, Tokens); the machine and the background jobs at the end (ESTUDO-03 §4.3).
    expect(panelNames(right)).toEqual([
      "Conversation tasks", "Changes", "Tools", "Context", "Environment", "Connectors", "Tokens",
      "This machine", "Background jobs",
    ]);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Tokens", "Memory", "Fusion"]);
    // The state line: no drag handle, no move, no close.
    expect(right.parentElement).toHaveTextContent("idle");
    expect(screen.queryByRole("button", { name: /Status/ })).not.toBeInTheDocument();
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

  it("hides a panel's frame when its content has nothing to say, rather than leaving an empty box", () => {
    mount();
    // Background jobs with no job renders nothing; the frame hides itself with a :has() rule.
    const jobs = screen.getByRole("region", { name: "Background jobs" });
    expect(jobs.querySelector("[data-panel-body]")?.childElementCount).toBe(0);
    expect(jobs.className).toContain("has-[>[data-panel-body]:empty]:hidden");
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
    // Not a "—", not "No tools": no frame at all, so no title or buttons either. The last turn's tab
    // group has no member with anything to say, so the whole group is gone with them.
    for (const id of SILENT) expect(right().querySelector(`[data-panel="${id}"]`)).toBeNull();
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
    for (const id of SILENT) expect(right().querySelector(`[data-panel="${id}"]`)).toBeNull();
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

  it("names the panel in sentence case, with a short title where the full one does not fit", () => {
    mount();
    for (const id of ["conversation.tasks", "activity.machine", "activity.jobs", "activity.tools"]) {
      const section = document.querySelector(`section[data-panel="${id}"]`) as HTMLElement;
      const title = section.querySelector("span[title]") as HTMLElement;
      expect(title.className).not.toMatch(/uppercase|tracking-/);
      // The full name stays the panel's accessible name and its pointer tooltip.
      expect(title.getAttribute("title")).toBe(section.getAttribute("aria-label"));
    }
    const shown = (id: string) => (document.querySelector(`section[data-panel="${id}"] span[title]`) as HTMLElement).textContent;
    expect(shown("conversation.tasks")).toBe("Tasks");
    expect(shown("activity.machine")).toBe("Machine");
    expect(shown("activity.jobs")).toBe("Jobs");
    expect(shown("activity.tools")).toBe("Tools");
    // The tabs drop the capitals too.
    for (const tab of screen.getAllByRole("tab")) expect(tab.className).not.toMatch(/uppercase|tracking-/);
  });

  it("keeps drag and close always shown, and shows move, maximise and minimise on hover or focus", () => {
    mount();
    const tools = screen.getByRole("region", { name: "Tools" });
    const controls = tools.querySelector("[data-panel-controls]") as HTMLElement;
    expect(within(controls).getByRole("button", { name: "Move Tools" })).toBeInTheDocument();
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
    // And back: the close button, always shown, is where a reverse Tab enters the panel.
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
    expect(dropTarget(layout, "activity.memory")).toEqual({ zone: "right", index: 7 });
    expect(dropTarget(layout, "zone:right")).toEqual({ zone: "right", index: 11 });
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
