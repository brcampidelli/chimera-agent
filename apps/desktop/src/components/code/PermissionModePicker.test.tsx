import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import { PermissionModePicker } from "@/components/code/PermissionModePicker";
import { renderWithProviders } from "@/test/utils";

const OPEN = { name: "Permissions: Manual" };

function mount(overrides: Partial<ComponentProps<typeof PermissionModePicker>> = {}) {
  const props = {
    value: "manual" as const,
    onChange: vi.fn(),
    bypassListed: false,
    ...overrides,
  };
  renderWithProviders(<PermissionModePicker {...props} />);
  return props;
}

describe("the permission-mode selector under the prompt", () => {
  it("shows the mode the conversation runs under", () => {
    mount({ value: "accept_edits" });
    expect(screen.getByRole("button", { name: "Permissions: Accept edits" })).toBeInTheDocument();
  });

  it("lists the five modes, and Ignore permissions only when the owner released it", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", OPEN));
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toEqual([
      expect.stringContaining("Manual"),
      expect.stringContaining("Accept edits"),
      expect.stringContaining("Plan"),
      expect.stringContaining("Auto"),
      expect.stringContaining("Don't ask"),
    ]);
    expect(screen.queryByRole("option", { name: /Ignore permissions/ })).not.toBeInTheDocument();
  });

  it("offers Ignore permissions once the owner released it", async () => {
    const user = userEvent.setup();
    mount({ bypassListed: true, value: "bypass" });
    await user.click(screen.getByRole("button", { name: "Permissions: Ignore permissions" }));
    expect(screen.getByRole("option", { name: /Ignore permissions/ })).toBeInTheDocument();
  });

  it("choosing another mode reports it once and closes the list", async () => {
    const user = userEvent.setup();
    const props = mount();
    await user.click(screen.getByRole("button", OPEN));
    await user.click(screen.getByRole("option", { name: /Plan/ }));
    expect(props.onChange).toHaveBeenCalledTimes(1);
    expect(props.onChange).toHaveBeenCalledWith("plan");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("choosing the mode already in force reports nothing", async () => {
    const user = userEvent.setup();
    const props = mount({ value: "auto" });
    await user.click(screen.getByRole("button", { name: "Permissions: Auto" }));
    await user.click(screen.getByRole("option", { name: /Auto/ }));
    expect(props.onChange).not.toHaveBeenCalled();
  });

  it("while the project holds Ignore permissions, every other mode is locked", async () => {
    const user = userEvent.setup();
    const props = mount({ bypassListed: true, locked: true, value: "bypass" });
    await user.click(screen.getByRole("button", { name: "Permissions: Ignore permissions" }));
    expect(screen.getByRole("option", { name: /Manual/ })).toBeDisabled();
    expect(screen.getByRole("option", { name: /Plan/ })).toBeDisabled();
    expect(screen.getByText(/Ignoring permissions is on for the whole project/)).toBeInTheDocument();
    expect(props.onChange).not.toHaveBeenCalled();
  });

  it("a request from the shortcut opens the list, and is taken once", async () => {
    const handled = vi.fn();
    const { rerender } = renderWithProviders(
      <PermissionModePicker value="manual" onChange={vi.fn()} bypassListed={false} openRequest={false} />,
    );
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    rerender(
      <PermissionModePicker
        value="manual"
        onChange={vi.fn()}
        bypassListed={false}
        openRequest
        onOpenRequestHandled={handled}
      />,
    );
    await waitFor(() => expect(screen.getByRole("listbox")).toBeInTheDocument());
    expect(handled).toHaveBeenCalledOnce();
  });

  it("Escape closes the list and returns focus to its trigger", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", OPEN));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", OPEN)).toHaveFocus();
  });

  it("opens on the mode in force, and the arrows move between the choices", async () => {
    const user = userEvent.setup();
    mount({ value: "accept_edits" });
    await user.click(screen.getByRole("button", { name: "Permissions: Accept edits" }));
    expect(screen.getByRole("option", { name: /Accept edits/ })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { name: /Plan/ })).toHaveFocus();
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(screen.getByRole("option", { name: /Manual/ })).toHaveFocus();
  });

  it("a locked list opens on Ignore permissions, the one choice left, and the arrows never reach a locked row", async () => {
    const user = userEvent.setup();
    mount({ bypassListed: true, locked: true, value: "bypass" });
    await user.click(screen.getByRole("button", { name: "Permissions: Ignore permissions" }));
    expect(screen.getByRole("option", { name: /Ignore permissions/ })).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("option", { name: /Ignore permissions/ })).toHaveFocus();
  });
});
