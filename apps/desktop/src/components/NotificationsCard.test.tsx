import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsCard } from "@/components/NotificationsCard";
import { NOTIFY_MIN_SECONDS_KEY, readFlag, readMinSeconds, writeMinSeconds } from "@/lib/notify";
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
    const threshold = screen.getByRole("combobox", { name: /only for turns longer than/i });
    expect(threshold).toHaveValue("0");
    // Nothing to apply a threshold to while turn notifications are off.
    expect(threshold).toBeDisabled();
    expect(screen.getByRole("option", { name: /every turn/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /over 30 seconds/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /over 1 minute/i })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /over 5 minutes/i })).toBeInTheDocument();
    expect(requestPermission).not.toHaveBeenCalled();
  });

  it("keeps a previously stored nonstandard threshold selectable", async () => {
    localStorage.setItem("chimera.notifyOnFinish", "1");
    localStorage.setItem(NOTIFY_MIN_SECONDS_KEY, "45");
    const user = userEvent.setup();
    renderWithProviders(<NotificationsCard />);

    const threshold = screen.getByRole("combobox", { name: "Only for turns longer than (seconds)" });
    expect(threshold).toHaveValue("45");
    expect(within(threshold).getByRole("option", { name: "Custom (45 seconds)" })).toHaveValue("45");
    await user.selectOptions(threshold, "0");
    expect(readMinSeconds()).toBe(0);
  });

  it("offers the threshold choices in General using the registered strings", () => {
    renderWithProviders(<NotificationsCard />);

    const threshold = screen.getByRole("combobox", { name: "Only for turns longer than (seconds)" });
    expect(within(threshold).getByRole("option", { name: "Every turn" })).toHaveValue("0");
    expect(within(threshold).getByRole("option", { name: "Over 30 seconds" })).toHaveValue("30");
    expect(within(threshold).getByRole("option", { name: "Over 1 minute" })).toHaveValue("60");
    expect(within(threshold).getByRole("option", { name: "Over 5 minutes" })).toHaveValue("300");
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

    const threshold = screen.getByRole("combobox", { name: /only for turns longer than/i });
    await user.selectOptions(threshold, "60");
    expect(readMinSeconds()).toBe(60);
    expect(localStorage.getItem(NOTIFY_MIN_SECONDS_KEY)).toBe("60");
  });

  it("follows a threshold changed elsewhere and preserves a saved custom value", async () => {
    localStorage.setItem("chimera.notifyOnFinish", "1");
    const user = userEvent.setup();
    renderWithProviders(<NotificationsCard />);
    const threshold = screen.getByRole("combobox", { name: /only for turns longer than/i });

    // Another window (or the conversation's header) writes the shared value.
    act(() => writeMinSeconds(30));
    expect(threshold).toHaveValue("30");
    // A standard value is its own option, not also a "Custom" duplicate of it.
    expect(screen.queryByRole("option", { name: /custom/i })).not.toBeInTheDocument();
    act(() => writeMinSeconds(45));
    expect(threshold).toHaveValue("45");
    expect(screen.getByRole("option", { name: /custom \(45 seconds\)/i })).toBeInTheDocument();

    await user.selectOptions(threshold, "300");
    expect(readMinSeconds()).toBe(300);
  });
});
