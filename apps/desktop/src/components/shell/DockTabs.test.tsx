import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Activity } from "@/components/Activity";
import { AppShell } from "@/components/shell/AppShell";
import { dockUnits, splitUnits } from "@/components/shell/Dock";
import { isDockPanel } from "@/components/shell/panels";
import { ToastProvider } from "@/components/ui/toast";
import { AgentProvider } from "@/lib/agent-context";
import { applyLayout, defaultLayout, panelsIn, type Layout, type LayoutAction, type PanelId } from "@/lib/layout/model";
import { STORAGE_KEY } from "@/lib/layout/store";
import { AGENT_AFTER_A_TURN, withoutDefaultTabs } from "@/test/agent";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getApprovals: vi.fn(async () => []),
  getResources: vi.fn(async () => {
    throw new Error("not in this test");
  }),
  listJobs: vi.fn(async () => ({ jobs: [] })),
}));

/**
 * C9: the panels of one dock can become tabs of one frame, and a dock can be split in two halves.
 * What is checked here is what the person meets: the menu that does it, the keyboard on the tabs, the
 * divider, and that all of it survives a relaunch, while a layout saved before any of this opens as it
 * always did.
 */
function mount() {
  return renderWithProviders(
    <ToastProvider>
      <AgentProvider value={AGENT_AFTER_A_TURN}>
        <AppShell viewKey="code" viewLabel="Code" rail={<nav aria-label="rail" />} inspector={<Activity />}>
          <p>conversation</p>
        </AppShell>
      </AgentProvider>
    </ToastProvider>,
  );
}

/** The default without its "last turn" tab group, the right panel as the tests below group it. */
const UNGROUPED = [
  "Conversation tasks", "Changes", "Tools", "Context", "Environment", "Connectors", "Tokens", "Memory", "Fusion",
  "This machine", "Background jobs",
];

function ungroupedDefault(): Layout {
  return { ...defaultLayout(), tabGroups: [], activeTabs: {} };
}

function stored(): Layout {
  return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Layout;
}

function rightPanel() {
  return screen.getByRole("region", { name: "Right panel" });
}

function sectionNames(region: HTMLElement): string[] {
  return Array.from(region.querySelectorAll("section[data-panel]")).map((s) => s.getAttribute("aria-label") ?? "");
}

async function groupToolsWithTokens(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Move Tokens" }));
  await user.click(await screen.findByRole("menuitem", { name: "Group as tabs with Tools" }));
}

describe("panels as tabs", () => {
  // Grouping from scratch: the panels start side by side, not in the default's "last turn" group.
  beforeEach(() => {
    localStorage.clear();
    withoutDefaultTabs();
  });

  it("starts, by default, with the last turn's three cards as tabs of one frame", () => {
    localStorage.clear();
    mount();
    expect(within(screen.getByRole("tablist", { name: "Panel tabs" })).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Tokens", "Memory", "Fusion",
    ]);
    expect(sectionNames(rightPanel())).not.toContain("Memory");
  });

  it("groups two panels into one frame from the menu, with the moved one selected, and keeps it", async () => {
    const user = userEvent.setup();
    mount();
    await groupToolsWithTokens(user);

    const tabs = within(screen.getByRole("tablist", { name: "Panel tabs" })).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual(["Tools", "Tokens"]);
    expect(tabs.map((tab) => tab.getAttribute("aria-selected"))).toEqual(["false", "true"]);
    // One frame for the group, and only the selected tab's content drawn, inside the tab panel.
    // The group draws where its first member (Tools) was, as its selected tab.
    expect(sectionNames(rightPanel())).toEqual([
      "Conversation tasks", "Changes", "Tokens", "Context", "Environment", "Connectors", "Memory", "Fusion",
      "This machine", "Background jobs",
    ]);
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", tabs[1].id);
    expect(tabs[1]).toHaveAttribute("aria-controls", panel.id);
    expect(stored().tabGroups).toEqual([["activity.tools", "activity.tokens"]]);
    expect(stored().activeTabs["activity.tools"]).toBe("activity.tokens");
  });

  it("moves between tabs with the arrow keys, wrapping, and with Home and End", async () => {
    const user = userEvent.setup();
    mount();
    await groupToolsWithTokens(user);
    await user.click(screen.getByRole("button", { name: "Move Memory" }));
    await user.click(await screen.findByRole("menuitem", { name: "Group as tabs with Tools, Tokens" }));

    const selected = () => screen.getAllByRole("tab").find((tab) => tab.getAttribute("aria-selected") === "true");
    expect(selected()).toHaveTextContent("Memory");
    // Only the selected tab is in the Tab order.
    expect(screen.getAllByRole("tab").map((tab) => tab.tabIndex)).toEqual([-1, -1, 0]);

    selected()?.focus();
    await user.keyboard("{ArrowRight}");
    expect(selected()).toHaveTextContent("Tools");
    expect(selected()).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(selected()).toHaveTextContent("Memory");
    await user.keyboard("{Home}");
    expect(selected()).toHaveTextContent("Tools");
    await user.keyboard("{End}");
    expect(selected()).toHaveTextContent("Memory");
    expect(stored().activeTabs["activity.tools"]).toBe("activity.memory");
  });

  it("leaves Ctrl+Tab alone: it belongs to the conversation", async () => {
    const user = userEvent.setup();
    mount();
    await groupToolsWithTokens(user);
    const tokens = screen.getByRole("tab", { name: "Tokens" });
    tokens.focus();
    await user.keyboard("{Control>}{Tab}{/Control}");
    expect(screen.getByRole("tab", { name: "Tokens" })).toHaveAttribute("aria-selected", "true");
  });

  it("selects a tab with a click and takes a panel back out of the tabs", async () => {
    const user = userEvent.setup();
    mount();
    await groupToolsWithTokens(user);
    await user.click(screen.getByRole("tab", { name: "Tools" }));
    expect(screen.getByRole("tab", { name: "Tools" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByRole("button", { name: "Move Tools" }));
    await user.click(await screen.findByRole("menuitem", { name: "Take out of the tabs" }));
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(sectionNames(rightPanel())).toEqual(UNGROUPED);
    expect(stored().tabGroups).toEqual([]);
  });
});

describe("a split dock", () => {
  beforeEach(() => localStorage.clear());

  it("splits from a panel's menu, resizes from the keyboard, and opens the same way after a relaunch", async () => {
    const user = userEvent.setup();
    const { unmount } = mount();
    await user.click(screen.getByRole("button", { name: "Move Background jobs" }));
    await user.click(await screen.findByRole("menuitem", { name: "Split side by side" }));

    const divider = screen.getByRole("separator", { name: "Resize the split" });
    expect(divider).toHaveAttribute("aria-orientation", "vertical");
    expect(divider).toHaveAttribute("aria-valuenow", "50");
    const halves = rightPanel().querySelectorAll("[data-split-half]");
    expect(halves).toHaveLength(2);
    expect(sectionNames(halves[1] as HTMLElement)).toEqual(["Background jobs"]);

    divider.focus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(divider).toHaveAttribute("aria-valuenow", "60");
    // The layout clamps: a half can never be dragged to nothing.
    for (let i = 0; i < 10; i++) await user.keyboard("{ArrowRight}");
    expect(divider).toHaveAttribute("aria-valuenow", "80");
    expect(stored().splits.right).toEqual({ orientation: "horizontal", ratio: 0.8, second: ["activity.jobs"] });

    unmount();
    mount();
    expect(screen.getByRole("separator", { name: "Resize the split" })).toHaveAttribute("aria-valuenow", "80");
    expect(sectionNames(rightPanel().querySelectorAll("[data-split-half]")[1] as HTMLElement)).toEqual(["Background jobs"]);

    await user.click(screen.getByRole("button", { name: "Move Tools" }));
    await user.click(await screen.findByRole("menuitem", { name: "Undo the split" }));
    expect(screen.queryByRole("separator", { name: "Resize the split" })).not.toBeInTheDocument();
  });

  it("stacks top and bottom with an upright-keyed divider", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Move Context" }));
    await user.click(await screen.findByRole("menuitem", { name: "Split top and bottom" }));
    const divider = screen.getByRole("separator", { name: "Resize the split" });
    expect(divider).toHaveAttribute("aria-orientation", "horizontal");
    divider.focus();
    await user.keyboard("{ArrowDown}");
    expect(divider).toHaveAttribute("aria-valuenow", "55");
    await user.keyboard("{ArrowLeft}");
    expect(divider).toHaveAttribute("aria-valuenow", "55");
    await user.keyboard("{Home}");
    expect(divider).toHaveAttribute("aria-valuenow", "50");
  });
});

describe("a layout saved before tabs and splits", () => {
  beforeEach(() => localStorage.clear());

  it("opens exactly as it did: same panels, same order, no tabs, no divider", () => {
    // The order the default had then, with memory moved to the top: someone's own arrangement, which
    // the new default must not touch.
    const order: PanelId[] = [
      "activity.memory", "activity.tools", "activity.tokens", "activity.fusion", "activity.jobs", "activity.machine",
      "conversation.environment", "conversation.context", "conversation.connectors", "conversation.changes", "conversation.tasks",
    ];
    const arranged = ungroupedDefault();
    order.forEach((id, i) => (arranged.panels[id] = { zone: "right", order: i, mode: "open" }));
    const old = JSON.parse(JSON.stringify(arranged)) as Record<string, unknown>;
    delete old.tabGroups;
    delete old.activeTabs;
    delete old.splits;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(old));

    mount();
    expect(sectionNames(rightPanel())).toEqual(["Memory", "Tools", "Tokens", "Fusion", "Background jobs", "This machine", "Environment", "Context", "Connectors", "Changes", "Conversation tasks"]);
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
    expect(screen.queryByRole("separator", { name: "Resize the split" })).not.toBeInTheDocument();
  });
});

describe("what a dock draws", () => {
  const ids = (layout: Layout) => panelsIn(layout, "right").filter(isDockPanel);

  it("draws a group with one member left on screen as a lone panel", () => {
    const grouped = applyLayout(ungroupedDefault(), { type: "group-tabs", panels: ["activity.tools", "activity.tokens"] });
    const units = dockUnits(grouped, ids(grouped).filter((id) => id !== "activity.tokens"));
    expect(units.find((u) => u.key === "activity.tools")).toEqual({ key: "activity.tools", members: ["activity.tools"], anchor: null });
  });

  it("puts a group in the half that names it, as a whole", () => {
    const actions: LayoutAction[] = [
      { type: "group-tabs", panels: ["activity.tools", "activity.tokens"] },
      { type: "set-split", zone: "right", orientation: "horizontal", panel: "activity.tokens" },
    ];
    const layout = actions.reduce(applyLayout, ungroupedDefault());
    const split = layout.splits.right;
    if (!split) throw new Error("no split");
    const [first, second] = splitUnits(dockUnits(layout, ids(layout)), split);
    expect(second.map((u) => u.members)).toEqual([["activity.tools", "activity.tokens"]]);
    expect(first.map((u) => u.key)).toEqual([
      "conversation.tasks", "conversation.changes", "conversation.context", "conversation.environment",
      "conversation.connectors", "activity.memory", "activity.fusion", "activity.machine", "activity.jobs",
    ]);
  });
});
