import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Activity } from "@/components/Activity";
import { ComposerSettings } from "@/components/code/ComposerSettings";
import { AppShell } from "@/components/shell/AppShell";
import { announcements, dropTarget } from "@/components/shell/Dock";
import { ToastProvider } from "@/components/ui/toast";
import { AgentProvider } from "@/lib/agent-context";
import { useT } from "@/lib/i18n";
import { applyLayout, defaultLayout } from "@/lib/layout/model";
import { STORAGE_KEY } from "@/lib/layout/store";
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
function Shell() {
  return (
    <AppShell viewKey="code" viewLabel="Code" rail={<nav aria-label="rail" />} inspector={<Activity />}>
      <p>conversation</p>
    </AppShell>
  );
}

function mount() {
  return renderWithProviders(
    <ToastProvider>
      <AgentProvider>
        <Shell />
      </AgentProvider>
    </ToastProvider>,
  );
}

function panelNames(region: HTMLElement): string[] {
  return Array.from(region.querySelectorAll("section[data-panel]")).map((s) => s.getAttribute("aria-label") ?? "");
}

describe("the docks", () => {
  beforeEach(() => localStorage.clear());

  it("draws the right panel's sections as panels, in the layout's order, below a state line that is not one", () => {
    mount();
    const right = screen.getByRole("region", { name: "Right panel" });

    expect(panelNames(right)).toEqual(["Tools", "Tokens", "Memory", "Fusion", "Background jobs", "This machine"]);
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

    await user.click(screen.getByRole("button", { name: "Minimize Memory" }));
    const memory = screen.getByRole("region", { name: "Memory" });
    expect(memory.querySelector("[data-panel-body]")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Expand Memory" }));
    expect(screen.getByRole("region", { name: "Memory" }).querySelector("[data-panel-body]")).not.toBeNull();
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
    // Fusion without a fused turn renders nothing; the frame hides itself with a :has() rule.
    const fusion = screen.getByRole("region", { name: "Fusion" });
    expect(fusion.querySelector("[data-panel-body]")?.childElementCount).toBe(0);
    expect(fusion.className).toContain("has-[>[data-panel-body]:empty]:hidden");
  });
});

describe("where a drop lands", () => {
  it("lands before the panel it is dropped on, and at the end of a zone dropped on directly", () => {
    const layout = defaultLayout();
    expect(dropTarget(layout, "activity.memory")).toEqual({ zone: "right", index: 2 });
    expect(dropTarget(layout, "zone:right")).toEqual({ zone: "right", index: 6 });
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

  it("minimise to one line of chips, and come back", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ComposerSettings summary={["Chimera", "balanced", "gpt-6-luna"]}>
        <p>the pickers</p>
      </ComposerSettings>,
    );

    await user.click(screen.getByRole("button", { name: "Minimize Who runs, cost and model" }));
    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
    expect(screen.getByTestId("composer-settings-line")).toHaveTextContent("Chimerabalancedgpt-6-luna");

    await user.click(screen.getByRole("button", { name: "Expand Who runs, cost and model" }));
    expect(screen.getByText("the pickers")).toBeInTheDocument();
  });
});
