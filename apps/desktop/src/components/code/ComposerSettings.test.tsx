import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { COMPOSER_SETTINGS_RULE_KEY, ComposerSettings } from "@/components/code/ComposerSettings";
import { applyLayout, defaultLayout } from "@/lib/layout/model";
import { loadLayout, saveLayout } from "@/lib/layout/store";
import { renderWithProviders } from "@/test/utils";

const SUMMARY = ["Chimera", "balanced", "gpt-6-luna", "Default"];

function mount(collapseWhen = false) {
  return renderWithProviders(
    <ComposerSettings summary={SUMMARY} collapseWhen={collapseWhen}>
      <p>the pickers</p>
    </ComposerSettings>,
  );
}

const EXPAND = { name: "Expand Who runs, cost and model" };

/**
 * C6 of study 34. One click on the chip line used to store "open" in the layout for good, so the
 * pickers, the routing note and the rest took half the composer on every visit after changing a
 * model once. Opening from the chips is for this visit now; "Keep open" is the remembered choice.
 */
describe("the composer's settings, open for a visit or kept open", () => {
  beforeEach(() => localStorage.clear());

  it("start closed, as one line of chips with every part of the summary", () => {
    mount();
    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
    expect(screen.getByTestId("composer-settings-line")).toHaveTextContent("Chimerabalancedgpt-6-lunaDefault");
  });

  it("opened from the chips, come back closed on the next visit and leave the layout minimised", async () => {
    const user = userEvent.setup();
    const first = mount();
    await user.click(screen.getByRole("button", EXPAND));
    expect(screen.getByText("the pickers")).toBeInTheDocument();
    expect(loadLayout().panels["composer.config"].mode).toBe("minimized");
    first.unmount();

    mount();
    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
  });

  it("fold back to the line when a turn starts", async () => {
    const user = userEvent.setup();
    const view = mount(false);
    await user.click(screen.getByRole("button", EXPAND));
    expect(screen.getByText("the pickers")).toBeInTheDocument();

    view.rerender(
      <ComposerSettings summary={SUMMARY} collapseWhen>
        <p>the pickers</p>
      </ComposerSettings>,
    );
    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
  });

  it("stay open across visits once kept open, and stop when un-kept", async () => {
    const user = userEvent.setup();
    const first = mount();
    await user.click(screen.getByRole("button", EXPAND));
    const keep = screen.getByRole("button", { name: "Keep open" });
    expect(keep).toHaveAttribute("aria-pressed", "false");
    await user.click(keep);
    expect(loadLayout().panels["composer.config"].mode).toBe("open");
    first.unmount();

    const second = mount();
    expect(screen.getByText("the pickers")).toBeInTheDocument();
    const unkeep = screen.getByRole("button", { name: "Stop keeping open" });
    expect(unkeep).toHaveAttribute("aria-pressed", "true");
    await user.click(unkeep);
    // Still open for the rest of this visit — the person is looking at it — but no longer kept.
    expect(screen.getByText("the pickers")).toBeInTheDocument();
    expect(loadLayout().panels["composer.config"].mode).toBe("minimized");
    second.unmount();

    mount();
    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
  });

  it("minimise closes a kept panel and forgets the keep", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", EXPAND));
    await user.click(screen.getByRole("button", { name: "Keep open" }));
    await user.click(screen.getByRole("button", { name: "Minimize Who runs, cost and model" }));
    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
    expect(loadLayout().panels["composer.config"].mode).toBe("minimized");
  });

  it("reset, once, a layout stored open before the rule — and respect a keep made after it", () => {
    // What a person who clicked the chips once before this change has on disk.
    saveLayout(applyLayout(defaultLayout(), { type: "set-mode", panel: "composer.config", mode: "open" }));
    const first = mount();
    expect(screen.queryByText("the pickers")).not.toBeInTheDocument();
    expect(loadLayout().panels["composer.config"].mode).toBe("minimized");
    expect(localStorage.getItem(COMPOSER_SETTINGS_RULE_KEY)).toBe("1");
    first.unmount();

    // Open again AFTER the rule was read is a choice made under it, and stays.
    saveLayout(applyLayout(defaultLayout(), { type: "set-mode", panel: "composer.config", mode: "open" }));
    mount();
    expect(screen.getByText("the pickers")).toBeInTheDocument();
  });
});
