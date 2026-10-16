import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Activity } from "@/components/Activity";
import { AppShell } from "@/components/shell/AppShell";
import { ToastProvider } from "@/components/ui/toast";
import { AgentProvider } from "@/lib/agent-context";
import { FloatHost } from "@/lib/float/host";
import { PANEL_IDS, type PanelId } from "@/lib/layout/model";
import { STORAGE_KEY, parseLayout } from "@/lib/layout/store";
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
 * A layout stored before the conversation's side cards existed (C5) opens with every panel it names
 * exactly where it was — zone, order, mode, tab groups and split — and the five cards after them.
 *
 * Written out as the JSON a 0.67 build stored, not derived from today's `defaultLayout()`: a layout
 * built from the current default already carries the new panels, and would prove nothing about one
 * that does not.
 */
const OLD_PANELS = {
  sessions: { zone: "left", order: 0, mode: "open" },
  "activity.tools": { zone: "bottom", order: 0, mode: "open" },
  "activity.tokens": { zone: "right", order: 0, mode: "open" },
  "activity.memory": { zone: "right", order: 1, mode: "open" },
  "activity.fusion": { zone: "right", order: 2, mode: "open" },
  "activity.jobs": { zone: "right", order: 3, mode: "closed" },
  "activity.machine": { zone: "right", order: 4, mode: "minimized" },
  viewer: { zone: "center", order: 0, mode: "open" },
  "composer.config": { zone: "composer", order: 0, mode: "minimized" },
} as const;

const OLD = {
  version: 1,
  regions: {
    rail: { visible: true, size: null },
    left: { visible: true, size: 260 },
    right: { visible: true, size: 320 },
    bottom: { visible: true, size: 180 },
    viewer: { visible: true, size: 448 },
  },
  panels: OLD_PANELS,
  cards: {},
  maximized: null,
  transcriptWidth: "wide",
  tabGroups: [["activity.tokens", "activity.memory"]],
  activeTabs: { "activity.tokens": "activity.memory" },
  splits: { right: { orientation: "vertical", ratio: 0.6, second: ["activity.fusion"] } },
  beforeFocus: null,
};

const CARDS: PanelId[] = [
  "conversation.environment",
  "conversation.context",
  "conversation.connectors",
  "conversation.changes",
  "conversation.tasks",
];

function panelIds(root: Element): string[] {
  return Array.from(root.querySelectorAll("section[data-panel]")).map((s) => s.getAttribute("data-panel") ?? "");
}

describe("a layout stored before the side cards", () => {
  beforeEach(() => localStorage.clear());

  it("keeps every panel it names as it was, its tabs and its split, and puts the cards after them", () => {
    const layout = parseLayout(JSON.parse(JSON.stringify(OLD)));

    for (const [id, state] of Object.entries(OLD_PANELS)) expect(layout.panels[id as PanelId]).toEqual(state);
    expect(layout.tabGroups).toEqual(OLD.tabGroups);
    expect(layout.activeTabs).toEqual(OLD.activeTabs);
    expect(layout.splits).toEqual(OLD.splits);
    expect(layout.regions.right.size).toBe(320);
    expect(layout.transcriptWidth).toBe("wide");

    const lastOld = Math.max(...Object.values(OLD_PANELS).filter((p) => p.zone === "right").map((p) => p.order));
    for (const id of CARDS) {
      expect(layout.panels[id].zone).toBe("right");
      expect(layout.panels[id].mode).toBe("open");
      expect(layout.panels[id].order).toBeGreaterThan(lastOld);
    }
    // No two panels of a zone share a place, so the order of the old ones cannot be shuffled by a tie.
    const rightOrders = PANEL_IDS.filter((id) => layout.panels[id].zone === "right").map((id) => layout.panels[id].order);
    expect(new Set(rightOrders).size).toBe(rightOrders.length);
  });

  it("draws the old arrangement unchanged on screen, with the cards in the first half after it", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(OLD));
    renderWithProviders(
      <ToastProvider>
        <AgentProvider>
          <AppShell viewKey="code" viewLabel="Code" rail={<nav aria-label="rail" />} inspector={<Activity />}>
            <p>conversation</p>
          </AppShell>
        </AgentProvider>
      </ToastProvider>,
    );

    expect(panelIds(screen.getByRole("region", { name: "Bottom dock" }))).toEqual(["activity.tools"]);
    const right = screen.getByRole("region", { name: "Right panel" });
    const [first, second] = Array.from(right.querySelectorAll("[data-split-half]"));
    // The tab group draws as its selected tab (Memory); Background jobs stays closed.
    expect(panelIds(first)).toEqual(["activity.memory", "activity.machine", ...CARDS]);
    expect(panelIds(second)).toEqual(["activity.fusion"]);
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Tokens", "Memory"]);
    expect(right.querySelector('[data-panel="activity.jobs"]')).toBeNull();
  });
});

describe("a side card in its dock", () => {
  beforeEach(() => localStorage.clear());

  function mountShell() {
    return renderWithProviders(
      <ToastProvider>
        <AgentProvider>
          <FloatHost>
            <AppShell viewKey="code" viewLabel="Code" rail={<nav aria-label="rail" />} inspector={<Activity />}>
              <p>conversation</p>
            </AppShell>
          </FloatHost>
        </AgentProvider>
      </ToastProvider>,
    );
  }

  it("minimises, moves and closes like every other panel, and is not offered a window of its own", async () => {
    const user = userEvent.setup();
    mountShell();
    expect(screen.getByRole("button", { name: "Minimize Environment" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Close Environment" })).toBeInTheDocument();

    // Another panel is offered the window, so its absence below is the card's rule, not the test's.
    await user.click(screen.getByRole("button", { name: "Move Tools" }));
    expect(await screen.findByRole("menuitem", { name: "To a window of its own" })).toBeInTheDocument();
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Move Environment" }));
    expect(await screen.findByRole("menuitem", { name: "To the bottom dock" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "To a window of its own" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "To the bottom dock" }));
    expect(panelIds(await screen.findByRole("region", { name: "Bottom dock" }))).toEqual(["conversation.environment"]);

    await user.click(screen.getByRole("button", { name: "Close Environment" }));
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}").panels["conversation.environment"].mode).toBe("closed");
  });
});
