/** Browser pairing on the Security screen: the desktop shows a code and manages the paired browsers;
 *  a browser tab only says so. The code counts down and is shown once, from the response that made it. */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserScreenPairing } from "@/components/BrowserScreenPairing";
import {
  listBrowserScreenSessions,
  mintBrowserScreenCode,
  revokeAllBrowserScreenSessions,
  revokeBrowserScreenSession,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  mintBrowserScreenCode: vi.fn(),
  listBrowserScreenSessions: vi.fn(),
  revokeBrowserScreenSession: vi.fn(async () => ({ ok: true, revoked: 1 })),
  revokeAllBrowserScreenSessions: vi.fn(async () => ({ ok: true, revoked: 2 })),
}));

const NOW = 1_700_000_000;

function session(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    name: "Windows",
    created_at: NOW - 60,
    last_used_at: NOW - 10,
    last_ip: "",
    expires_at: NOW + 43_200,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.__CHIMERA_SCREEN_TOKEN__ = "screen";
  vi.mocked(listBrowserScreenSessions).mockResolvedValue({ sessions: [], seconds: 43_200 } as never);
  vi.mocked(mintBrowserScreenCode).mockResolvedValue({
    code: "ABCD-EFGH",
    expires_at: Date.now() / 1000 + 300,
    seconds: 300,
  } as never);
});

afterEach(() => {
  delete window.__CHIMERA_SCREEN_TOKEN__;
});

describe("browser pairing on the desktop", () => {
  it("shows a one-time code with a countdown when asked", async () => {
    renderWithProviders(<BrowserScreenPairing />);
    await userEvent.click(await screen.findByRole("button", { name: "Show pairing code" }));

    expect(await screen.findByTestId("browser-pairing-code")).toHaveTextContent("ABCD-EFGH");
    expect(screen.getByText(/Expires in \d+:\d{2}/)).toBeInTheDocument();
    expect(mintBrowserScreenCode).toHaveBeenCalledTimes(1);
  });

  it("lists the paired browsers, and revokes one by its id", async () => {
    vi.mocked(listBrowserScreenSessions).mockResolvedValue({
      sessions: [session()],
      seconds: 43_200,
    } as never);
    renderWithProviders(<BrowserScreenPairing />);

    expect(await screen.findByText("Windows")).toBeInTheDocument();
    expect(screen.getByText("Paired browsers (1)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(revokeBrowserScreenSession).toHaveBeenCalledWith("s1"));
  });

  it("revokes every paired browser at once", async () => {
    vi.mocked(listBrowserScreenSessions).mockResolvedValue({
      sessions: [session(), session({ id: "s2", name: "Mac" })],
      seconds: 43_200,
    } as never);
    renderWithProviders(<BrowserScreenPairing />);

    await userEvent.click(await screen.findByRole("button", { name: "Revoke all" }));
    await waitFor(() => expect(revokeAllBrowserScreenSessions).toHaveBeenCalledTimes(1));
  });

  it("says a browser tab cannot manage pairing, and offers no controls", async () => {
    delete window.__CHIMERA_SCREEN_TOKEN__;
    renderWithProviders(<BrowserScreenPairing />);

    expect(
      screen.getByText("This is a browser tab. Pairing codes are made and revoked in the desktop app."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show pairing code" })).toBeNull();
    expect(listBrowserScreenSessions).not.toHaveBeenCalled();
  });
});
