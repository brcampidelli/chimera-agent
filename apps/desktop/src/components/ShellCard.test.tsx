import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ShellCard } from "@/components/ShellCard";
import type { ShellPrefs } from "@/lib/types";
import { renderWithProviders as render } from "@/test/utils";

/**
 * The tray's four switches on the Settings screen (study 29 settings parity).
 *
 * Only the tray menu could change them, because they live in the desktop shell's own file and the
 * window has no IPC to the shell. The backend now writes that file for the screen; these tests hold
 * the screen half: each switch writes its own key and nothing else, start-at-sign-in shows the
 * operating system's answer rather than the click, and a server the shell did not start offers
 * nothing to flip.
 */
vi.mock("@/lib/api", () => ({ getShellPrefs: vi.fn(), patchShellPrefs: vi.fn() }));

const api = await import("@/lib/api");

function prefs(over: Partial<ShellPrefs> = {}): ShellPrefs {
  return {
    available: true,
    keep_in_tray: false,
    call_attention: true,
    quick_entry: false,
    quick_entry_chord: "CommandOrControl+Shift+Space",
    start_at_sign_in: false,
    sign_in_requested: null,
    problem: "",
    unreadable: false,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getShellPrefs).mockResolvedValue(prefs());
  vi.mocked(api.patchShellPrefs).mockImplementation(async (change) => prefs(change as Partial<ShellPrefs>));
});

describe("the tray's switches on the Settings screen", () => {
  it("shows each switch as the shell reads it", async () => {
    render(<ShellCard />);

    expect(await screen.findByRole("switch", { name: "Keep running in the tray" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByRole("switch", { name: "Flash when an approval is waiting" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByRole("switch", { name: "Quick-entry shortcut" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/Ctrl\+Shift\+Space brings Chimera forward/)).toBeInTheDocument();
  });

  it.each([
    ["Keep running in the tray", { keep_in_tray: true }],
    ["Flash when an approval is waiting", { call_attention: false }],
    ["Quick-entry shortcut", { quick_entry: true }],
    ["Start when you sign in", { start_at_sign_in: true }],
  ])("writes only its own key when %s is flipped", async (name, change) => {
    render(<ShellCard />);

    await userEvent.click(await screen.findByRole("switch", { name }));

    expect(api.patchShellPrefs).toHaveBeenCalledTimes(1);
    expect(api.patchShellPrefs).toHaveBeenCalledWith(change);
  });

  it("shows sign-in as the system answered, and says when a request is still out", async () => {
    // The screen asked for on; the shell has not carried it out yet, and the OS still says off.
    vi.mocked(api.getShellPrefs).mockResolvedValue(prefs({ start_at_sign_in: false, sign_in_requested: true }));
    render(<ShellCard />);

    expect(await screen.findByText("Asked; waiting for the app to apply it.")).toBeInTheDocument();
  });

  it("reads a refused sign-in entry as off, with the tray's own line", async () => {
    vi.mocked(api.getShellPrefs).mockResolvedValue(
      prefs({ start_at_sign_in: false, problem: "could not register start at sign-in: access denied" }),
    );
    render(<ShellCard />);

    expect(await screen.findByRole("switch", { name: "Start when you sign in" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "The app reports: could not register start at sign-in: access denied",
    );
  });

  it("offers nothing to flip on a server the desktop app did not start", async () => {
    vi.mocked(api.getShellPrefs).mockResolvedValue(prefs({ available: false, start_at_sign_in: null }));
    render(<ShellCard />);

    expect(await screen.findByText(/this server was not started by it/)).toBeInTheDocument();
    for (const control of screen.getAllByRole("switch")) expect(control).toBeDisabled();
  });

  it("saves nothing over a preferences file that does not parse", async () => {
    vi.mocked(api.getShellPrefs).mockResolvedValue(prefs({ unreadable: true }));
    render(<ShellCard />);

    expect(await screen.findByText(/does not parse/)).toBeInTheDocument();
    for (const control of screen.getAllByRole("switch")) expect(control).toBeDisabled();
  });

  it("renders nothing against a server that predates the route", async () => {
    vi.mocked(api.getShellPrefs).mockRejectedValue(new Error("404"));
    const { container } = render(<ShellCard />);

    await vi.waitFor(() => expect(api.getShellPrefs).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
