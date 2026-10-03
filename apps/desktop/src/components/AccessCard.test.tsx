/** Every way into this machine, on one card (study 29, P5.5).
 *
 * The card reads `GET /api/security/access` and offers only controls that close something: revoke a
 * link (by its id, never its token), revoke every link, close the network door, give the bridge a
 * new token. The bearer and the sharing settings are changed on the General tab, which "Change"
 * goes to — a button that goes nowhere is not offered.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessCard } from "@/components/AccessCard";
import {
  closeNetworkShare,
  getAccess,
  revokeAccessLink,
  revokeAllAccessLinks,
  rotateBridgeToken,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getAccess: vi.fn(),
  rotateBridgeToken: vi.fn(async () => ({})),
  revokeAccessLink: vi.fn(async () => ({ ok: true })),
  revokeAllAccessLinks: vi.fn(async () => ({ revoked: 2 })),
  closeNetworkShare: vi.fn(async () => ({ open: false, port: null, urls: [] })),
}));

function access(over: Record<string, unknown> = {}) {
  return {
    server_token: { set: false },
    bridge: { enabled: false, active: false, tier: null, hint: "" },
    sharing: { enabled: true, expiry_hours: null },
    guest_door: { open: false, port: null, urls: [] },
    links: [
      {
        id: "id-new", session_id: "s2", session_title: "deploy notes", label: "Bia",
        created_at: 1_700_000_100, expires_at: null, expired: false, hint: "…wxyz",
      },
      {
        id: "id-old", session_id: "s1", session_title: "", label: "",
        created_at: 1_700_000_000, expires_at: 1_700_003_600, expired: true, hint: "…abcd",
      },
    ],
    ...over,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAccess).mockResolvedValue(access());
});

describe("the access card", () => {
  it("lists every link with its conversation, its expiry and four characters of it, no more", async () => {
    renderWithProviders(<AccessCard />);
    const rows = await screen.findAllByTestId("access-link");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Bia");
    expect(rows[0]).toHaveTextContent("deploy notes");
    expect(rows[0]).toHaveTextContent("never expires");
    expect(rows[0]).toHaveTextContent("…wxyz");
    // A link with no label and no title still says what it is; an expired one says so.
    expect(rows[1]).toHaveTextContent("No label");
    expect(rows[1]).toHaveTextContent("Untitled conversation");
    expect(rows[1]).toHaveTextContent("Expired");
    expect(screen.getByText("Share links (2)")).toBeInTheDocument();
  });

  it("revokes one link by its id and every link with one button", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccessCard />);
    const rows = await screen.findAllByTestId("access-link");
    await user.click(within(rows[1]).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(revokeAccessLink).toHaveBeenCalled());
    expect(vi.mocked(revokeAccessLink).mock.calls[0][0]).toBe("id-old");

    await user.click(screen.getByRole("button", { name: "Revoke all" }));
    await waitFor(() => expect(revokeAllAccessLinks).toHaveBeenCalledTimes(1));
    // Each control re-reads the card rather than guessing what is left.
    await waitFor(() => expect(vi.mocked(getAccess).mock.calls.length).toBeGreaterThanOrEqual(3));
  });

  it("says when there are no links, and offers no Revoke all", async () => {
    vi.mocked(getAccess).mockResolvedValue(access({ links: [] }));
    renderWithProviders(<AccessCard />);
    expect(await screen.findByText("No share links.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke all" })).not.toBeInTheDocument();
  });

  it("says an unset bearer leaves the app open to this computer, and Change goes to Settings", async () => {
    const open = vi.fn();
    renderWithProviders(<AccessCard onOpenSettings={open} />);
    expect(
      await screen.findByText("Not set. Any program on this computer can call the app."),
    ).toBeInTheDocument();
    await userEvent.setup().click(screen.getAllByRole("button", { name: "Change" })[0]);
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("offers no Change button when there is nowhere to go", async () => {
    renderWithProviders(<AccessCard />);
    await screen.findAllByTestId("access-link");
    expect(screen.queryByRole("button", { name: "Change" })).not.toBeInTheDocument();
  });

  it("gives the bridge a new token only while it is on", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<AccessCard />);
    expect(await screen.findByText("Off. Claude cannot operate this app.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New token" })).toBeDisabled();
    unmount();

    vi.mocked(getAccess).mockResolvedValue(
      access({ bridge: { enabled: true, active: true, tier: "full", hint: "…Q9zk" } }),
    );
    renderWithProviders(<AccessCard />);
    expect(await screen.findByText("On, full control. Token …Q9zk.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "New token" }));
    await waitFor(() => expect(rotateBridgeToken).toHaveBeenCalledTimes(1));
    expect(
      await screen.findByText(/New token issued\. The old one no longer works/),
    ).toBeInTheDocument();
  });

  it("closes an open network door, and offers no Close while it is shut", async () => {
    renderWithProviders(<AccessCard />);
    expect(
      await screen.findByText("Closed. Share links open only from this computer."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close" })).not.toBeInTheDocument();

    vi.mocked(getAccess).mockResolvedValue(
      access({ guest_door: { open: true, port: 8123, urls: ["http://10.0.0.2:8123/"] } }),
    );
    renderWithProviders(<AccessCard />);
    expect(await screen.findByText(/Open on port 8123/)).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(closeNetworkShare).toHaveBeenCalledTimes(1));
  });

  it("names the sharing state: off, and on with an expiry", async () => {
    vi.mocked(getAccess).mockResolvedValue(access({ sharing: { enabled: false, expiry_hours: null } }));
    const { unmount } = renderWithProviders(<AccessCard />);
    expect(
      await screen.findByText("Off. No link opens and no new one can be made."),
    ).toBeInTheDocument();
    unmount();
    vi.mocked(getAccess).mockResolvedValue(access({ sharing: { enabled: true, expiry_hours: 24 } }));
    renderWithProviders(<AccessCard />);
    expect(await screen.findByText("On. New links stop working after 24 h.")).toBeInTheDocument();
  });
});
