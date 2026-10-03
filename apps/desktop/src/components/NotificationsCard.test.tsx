import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsCard } from "@/components/NotificationsCard";
import { readFlag, readMinSeconds, writeMinSeconds } from "@/lib/notify";
import { renderWithProviders } from "@/test/utils";

/**
 * Settings › General › Notifications.
 *
 * Every switch is off until the person turns it on, and turning one on is when the operating
 * system is asked — from the click, which is the moment a person is ready to answer it.
 */
describe("NotificationsCard", () => {
  let requestPermission: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.clear();
    requestPermission = vi.fn(async () => "granted");
    vi.stubGlobal("Notification", Object.assign(vi.fn(), { permission: "default", requestPermission }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("starts with every notification off and the threshold at zero, as before the card existed", () => {
    renderWithProviders(<NotificationsCard />);

    for (const name of [/when a turn ends/i, /waits for my approval/i, /when a schedule fails/i]) {
      expect(screen.getByRole("switch", { name })).toHaveAttribute("aria-checked", "false");
    }
    const threshold = screen.getByLabelText(/only for turns longer than/i);
    expect(threshold).toHaveValue(0);
    // Nothing to apply a threshold to while turn notifications are off.
    expect(threshold).toBeDisabled();
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("asks the operating system for permission when a notification is switched on", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NotificationsCard />);

    await user.click(screen.getByRole("switch", { name: /waits for my approval/i }));

    expect(requestPermission).toHaveBeenCalledTimes(1);
    expect(readFlag("chimera.notifyApprovals")).toBe(true);
    expect(screen.getByRole("switch", { name: /waits for my approval/i })).toHaveAttribute("aria-checked", "true");
  });

  it("says so when the system blocks notifications, instead of leaving the switch to fail in silence", async () => {
    requestPermission.mockResolvedValue("denied");
    const user = userEvent.setup();
    renderWithProviders(<NotificationsCard />);

    await user.click(screen.getByRole("switch", { name: /when a schedule fails/i }));

    expect(await screen.findByText(/blocking notifications for chimera/i)).toBeInTheDocument();
  });

  it("writes the end-of-turn switch to the same preference as the conversation's header button", async () => {
    const user = userEvent.setup();
    renderWithProviders(<NotificationsCard />);

    await user.click(screen.getByRole("switch", { name: /when a turn ends/i }));
    expect(localStorage.getItem("chimera.notifyOnFinish")).toBe("1");

    const threshold = screen.getByLabelText(/only for turns longer than/i);
    await user.clear(threshold);
    await user.type(threshold, "45");
    expect(readMinSeconds()).toBe(45);
  });

  it("follows a threshold changed elsewhere, and settles a fraction on the whole seconds it stored", async () => {
    localStorage.setItem("chimera.notifyOnFinish", "1");
    const user = userEvent.setup();
    renderWithProviders(<NotificationsCard />);
    const threshold = screen.getByLabelText(/only for turns longer than/i);

    // Another window (or the conversation's header) writes the shared value.
    act(() => writeMinSeconds(30));
    expect(threshold).toHaveValue(30);

    await user.clear(threshold);
    await user.type(threshold, "1.5");
    await user.tab();
    expect(readMinSeconds()).toBe(1);
    expect(threshold).toHaveValue(1);
  });
});
