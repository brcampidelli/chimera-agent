import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { HiddenTray } from "@/components/shell/HiddenTray";
import { useLayout } from "@/lib/layout/context";
import type { LayoutAction } from "@/lib/layout/model";
import { STORAGE_KEY } from "@/lib/layout/store";
import { renderWithProviders } from "@/test/utils";

/**
 * The way back to anything hidden, from the status bar. Nothing at zero, for the reason
 * `PendingApprovals` gives; a count, a list with "Show", and "Restore default layout" otherwise.
 */
let dispatchRef: ((a: LayoutAction) => boolean) | null = null;

function Harness() {
  const { dispatch } = useLayout();
  dispatchRef = dispatch;
  return <HiddenTray />;
}

function hide(...actions: LayoutAction[]) {
  act(() => {
    for (const a of actions) dispatchRef?.(a);
  });
}

describe("HiddenTray", () => {
  beforeEach(() => {
    localStorage.clear();
    dispatchRef = null;
  });

  it("renders nothing while nothing is hidden", () => {
    renderWithProviders(<Harness />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the count, and lists what is hidden by name", async () => {
    renderWithProviders(<Harness />);
    hide(
      { type: "set-region", region: "left", visible: false },
      { type: "set-mode", panel: "activity.tools", mode: "closed" },
    );

    const trigger = screen.getByRole("button", { name: "Hidden: 2" });
    expect(trigger).toHaveTextContent("2");
    await userEvent.click(trigger);

    expect(await screen.findByRole("menuitem", { name: /Left sidebar/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Tools/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Show all" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Restore default layout" })).toBeInTheDocument();
  });

  it("brings one thing back with Show, and disappears when the last one is back", async () => {
    renderWithProviders(<Harness />);
    hide({ type: "set-mode", panel: "activity.memory", mode: "closed" });

    await userEvent.click(screen.getByRole("button", { name: "Hidden: 1" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Memory/ }));

    await waitFor(() => expect(screen.queryByRole("button", { name: /Hidden/ })).not.toBeInTheDocument());
  });

  it("offers Show all only when there is more than one thing to show", async () => {
    renderWithProviders(<Harness />);
    hide({ type: "set-region", region: "right", visible: false });

    await userEvent.click(screen.getByRole("button", { name: "Hidden: 1" }));

    await screen.findByRole("menuitem", { name: /Right panel/ });
    expect(screen.queryByRole("menuitem", { name: "Show all" })).not.toBeInTheDocument();
  });

  it("restores the default layout, which is also what gets stored", async () => {
    renderWithProviders(<Harness />);
    hide(
      { type: "set-region", region: "rail", visible: false },
      { type: "resize", region: "left", size: 400 },
    );

    await userEvent.click(screen.getByRole("button", { name: "Hidden: 1" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "Restore default layout" }));

    await waitFor(() => expect(screen.queryByRole("button", { name: /Hidden/ })).not.toBeInTheDocument());
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    expect(stored.regions.left.size).toBe(240);
    expect(stored.regions.rail.visible).toBe(true);
  });
});
