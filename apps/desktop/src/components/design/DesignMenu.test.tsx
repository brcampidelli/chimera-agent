import { screen } from "@testing-library/react";
import userEvent, { PointerEventsCheckLevel } from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DesignMenu, DesignMenuItem } from "@/components/design/DesignMenu";
import { renderWithProviders } from "@/test/utils";

function setup(onPick = vi.fn()) {
  const user = userEvent.setup();
  renderWithProviders(
    <>
      <p>outside</p>
      <DesignMenu
        label="Zoom: 100%"
        trigger={<span>100%</span>}
        items={
          <>
            <DesignMenuItem onSelect={() => onPick("Zoom in")}>Zoom in</DesignMenuItem>
            <DesignMenuItem onSelect={() => onPick("Fit")}>Fit</DesignMenuItem>
            <DesignMenuItem onSelect={() => onPick("Zoom out")}>Zoom out</DesignMenuItem>
          </>
        }
      />
    </>,
  );
  return { user, onPick, trigger: screen.getByRole("button", { name: "Zoom: 100%" }) };
}

describe("DesignMenu", () => {
  it("is a menu button: the trigger says what it opens, and a click runs the chosen item", async () => {
    const { user, onPick, trigger } = setup();
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.click(trigger);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Zoom in", "Fit", "Zoom out"]);

    await user.click(screen.getByRole("menuitem", { name: "Fit" }));
    expect(onPick).toHaveBeenCalledWith("Fit");
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("opens from the keyboard and moves with the arrows, Home and End", async () => {
    const { user, trigger } = setup();
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitem", { name: "Zoom in" })).toHaveFocus();

    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitem", { name: "Fit" })).toHaveFocus();
    await user.keyboard("{End}");
    expect(screen.getByRole("menuitem", { name: "Zoom out" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(screen.getByRole("menuitem", { name: "Zoom in" })).toHaveFocus();
    // Radix does not wrap: the arrow stops at the first item.
    await user.keyboard("{ArrowUp}");
    expect(screen.getByRole("menuitem", { name: "Zoom in" })).toHaveFocus();
  });

  it("runs the focused item on Enter", async () => {
    const { user, onPick, trigger } = setup();
    trigger.focus();
    await user.keyboard("{Enter}");
    await user.keyboard("{ArrowDown}");
    await user.keyboard("{Enter}");
    expect(onPick).toHaveBeenCalledWith("Fit");
  });

  it("closes on Escape and gives focus back to the trigger", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(trigger).toHaveFocus();
  });

  it("closes on a click outside", async () => {
    const { user, trigger } = setup();
    await user.click(trigger);
    // The open menu makes the rest of the page inert (pointer-events: none on the body), which is
    // what stops a click reaching the page; the dismissal is the click itself, so the check is off.
    await userEvent.setup({ pointerEventsCheck: PointerEventsCheckLevel.Never }).click(screen.getByText("outside"));
    expect(screen.queryByRole("menu")).toBeNull();
  });
});
