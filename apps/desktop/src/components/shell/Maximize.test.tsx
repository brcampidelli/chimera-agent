import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Activity } from "@/components/Activity";
import { AppShell } from "@/components/shell/AppShell";
import { ToastProvider } from "@/components/ui/toast";
import { AgentProvider } from "@/lib/agent-context";
import { useLayout } from "@/lib/layout/context";
import { PANELS, applyLayout, defaultLayout, type LayoutAction } from "@/lib/layout/model";
import { MINE_KEY, loadMine, saveMine } from "@/lib/layout/store";
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
 * Phase 5 of the dynamic screen: any panel maximises over the main area and Escape always restores it;
 * focus mode clears everything but the conversation and puts back exactly what was there; layouts are
 * one command away, the person's own included.
 */
let dispatch: ((a: LayoutAction) => boolean) | null = null;

function Shell() {
  dispatch = useLayout().dispatch;
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

describe("maximising", () => {
  beforeEach(() => {
    localStorage.clear();
    dispatch = null;
  });

  it("maximises a panel over the main area, once, and restores it from its own button", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByRole("button", { name: "Maximize Tools" }));

    // Drawn once, over the row: the dock no longer draws it too.
    expect(screen.getAllByRole("region", { name: "Tools" })).toHaveLength(1);
    expect(screen.getByRole("region", { name: "Tools" }).className).toContain("absolute");

    await user.click(screen.getByRole("button", { name: "Restore Tools" }));
    expect(screen.getByRole("region", { name: "Tools" }).className).not.toContain("absolute");
  });

  it("restores with Escape from anywhere, but leaves an Escape an open menu already handled", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Maximize This machine" }));

    const handled = new KeyboardEvent("keydown", { key: "Escape", cancelable: true });
    handled.preventDefault();
    act(() => void window.dispatchEvent(handled));
    expect(screen.getByRole("button", { name: "Restore This machine" })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Maximize This machine" })).toBeInTheDocument();
  });

  it("offers no maximise on a panel the layout says cannot", () => {
    mount();
    expect(PANELS["activity.tokens"].maximizable).toBe(false);
    expect(screen.queryByRole("button", { name: "Maximize Tokens" })).not.toBeInTheDocument();
  });
});

describe("focus mode from the status bar", () => {
  beforeEach(() => localStorage.clear());

  it("clears the rail and both side regions, and puts back exactly what was there", async () => {
    const user = userEvent.setup();
    mount();
    act(() => void dispatch?.({ type: "resize", region: "right", size: 400 }));

    await user.click(screen.getByRole("button", { name: "Focus mode" }));
    expect(screen.queryByRole("navigation", { name: "rail" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Right panel" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Leave focus mode" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "Leave focus mode" }));
    expect(screen.getByRole("navigation", { name: "rail" })).toBeInTheDocument();
    expect(screen.getByRole("separator", { name: "Resize the right panel" })).toHaveAttribute("aria-valuenow", "400");
  });
});

describe("layouts one command away", () => {
  it("review widens the viewer to its limit and puts the list away; monitor widens the right panel", () => {
    const review = applyLayout(defaultLayout(), { type: "preset", name: "review" });
    expect(review.regions.viewer.size).toBe(900);
    expect(review.regions.left.visible).toBe(false);

    const monitor = applyLayout(review, { type: "preset", name: "monitor" });
    expect(monitor.regions.right).toEqual({ visible: true, size: 480 });
  });

  it("keeps the person's own layout, without focus mode's memory, and reads it back like any stored one", () => {
    const store: Record<string, string> = {};
    const mine = applyLayout(applyLayout(defaultLayout(), { type: "preset", name: "monitor" }), { type: "toggle-focus" });

    expect(saveMine(mine, { setItem: (k, v) => void (store[k] = v) })).toBe(true);
    const back = loadMine({ getItem: (k) => store[k] ?? null });

    expect(back?.beforeFocus).toBeNull();
    expect(back?.regions.right.size).toBe(480);
    expect(Object.keys(store)).toEqual([MINE_KEY]);
  });

  it("has nothing to apply when nothing was saved, or when what was saved is not a layout", () => {
    expect(loadMine({ getItem: () => null })).toBeNull();
    expect(loadMine({ getItem: () => "{broken" })).toBeNull();
    expect(loadMine({ getItem: () => JSON.stringify({ version: 9 }) })).toEqual(defaultLayout());
  });

  it("applies a saved layout whole", () => {
    const saved = applyLayout(defaultLayout(), { type: "set-region", region: "rail", visible: false });
    expect(applyLayout(defaultLayout(), { type: "apply", layout: saved })).toBe(saved);
  });

  it("does not maximise the conversation list, which the Code screen draws with its own props", () => {
    const start = defaultLayout();
    expect(applyLayout(start, { type: "maximize", panel: "sessions" })).toBe(start);
  });
});
