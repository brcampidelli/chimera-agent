/** The owner's paired devices on the access card (S3, C10).
 *
 * The listener a phone types its code into is shut until the owner presses Open; "Pair a device"
 * is offered only while it is open; the code is shown once with its QR and its addresses; every
 * device has a Revoke, and Revoke all replaces the secret that signed them. No cookie and no token
 * is ever on the card.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccessCard } from "@/components/AccessCard";
import {
  closeDeviceDoor,
  getAccess,
  newPairingCode,
  openDeviceDoor,
  revokeAllDevices,
  revokeDevice,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getAccess: vi.fn(),
  rotateBridgeToken: vi.fn(async () => ({})),
  revokeAccessLink: vi.fn(async () => ({ ok: true })),
  revokeAllAccessLinks: vi.fn(async () => ({ revoked: 0 })),
  closeNetworkShare: vi.fn(async () => ({ open: false, port: null, urls: [] })),
  openDeviceDoor: vi.fn(async () => ({ open: true, port: 8123, urls: [] })),
  closeDeviceDoor: vi.fn(async () => ({ open: false, port: null, urls: [] })),
  newPairingCode: vi.fn(async () => ({
    code: "ABCD-EFGH",
    expires_at: 1_700_000_300,
    seconds: 300,
    urls: ["http://10.0.0.2:8123/pair?code=ABCD-EFGH"],
    qr: ["101", "010", "101"],
  })),
  revokeDevice: vi.fn(async () => ({ ok: true })),
  revokeAllDevices: vi.fn(async () => ({ revoked: 2 })),
}));

function access(over: Record<string, unknown> = {}) {
  return {
    server: { bind: "127.0.0.1", port: 8765, network: false },
    server_token: { set: true },
    bridge: { enabled: false, active: false, tier: null, hint: "" },
    sharing: { enabled: true, expiry_hours: null },
    guest_door: { open: false, port: null, urls: [] },
    links: [],
    device_door: { open: false, port: null, urls: [] },
    devices: [
      {
        id: "d-phone", name: "Bruno's phone", created_at: 1_700_000_000,
        last_used_at: 1_700_000_200, last_ip: "192.168.1.x", expires_at: 1_702_592_000,
      },
      {
        id: "d-tablet", name: "Tablet", created_at: 1_700_000_000,
        last_used_at: 1_700_000_100, last_ip: "", expires_at: 1_702_592_000,
      },
    ],
    ...over,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getAccess).mockResolvedValue(access());
});

describe("paired devices on the access card", () => {
  it("is shut by default, and offers no pairing until the owner opens it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccessCard />);
    expect(
      await screen.findByText("Closed. No phone or browser can pair, or reach the app as you."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pair a device" })).toBeDisabled();
    expect(openDeviceDoor).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Open" }));
    await waitFor(() => expect(openDeviceDoor).toHaveBeenCalledTimes(1));
    expect(newPairingCode).not.toHaveBeenCalled();
  });

  it("shows the code once with its QR and address, and says approving still needs a code", async () => {
    vi.mocked(getAccess).mockResolvedValue(
      access({ device_door: { open: true, port: 8123, urls: ["http://10.0.0.2:8123/"] } }),
    );
    const user = userEvent.setup();
    renderWithProviders(<AccessCard />);
    expect(await screen.findByText(/Open on port 8123/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Pair a device" }));
    const shown = await screen.findByTestId("pairing-code");
    expect(within(shown).getByText("ABCD-EFGH")).toBeInTheDocument();
    expect(within(shown).getByText("http://10.0.0.2:8123/pair?code=ABCD-EFGH")).toBeInTheDocument();
    const qr = within(shown).getByRole("img", { name: "QR code of the pairing address" });
    // Three dark modules in the corners and the centre of the 3x3 rows, each one square.
    expect(qr.querySelector("path")?.getAttribute("d")?.match(/h1v1h-1z/g)).toHaveLength(5);
    expect(
      screen.getByText("A paired device reads and talks as you. Approving still takes each question's code."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(closeDeviceDoor).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByTestId("pairing-code")).not.toBeInTheDocument());
  });

  it("lists every device with its last use, and revokes one by id or all of them", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccessCard />);
    const rows = await screen.findAllByTestId("access-device");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Bruno's phone");
    expect(rows[0]).toHaveTextContent("from 192.168.1.x");
    expect(rows[1]).not.toHaveTextContent("from");
    expect(screen.getByText("Paired devices (2)")).toBeInTheDocument();
    await user.click(within(rows[1]).getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(revokeDevice).toHaveBeenCalled());
    expect(vi.mocked(revokeDevice).mock.calls[0][0]).toBe("d-tablet");
    await user.click(screen.getByRole("button", { name: "Revoke all devices" }));
    await waitFor(() => expect(revokeAllDevices).toHaveBeenCalledTimes(1));
  });

  it("says when nothing is paired, and points away from home at a tunnel the owner runs", async () => {
    vi.mocked(getAccess).mockResolvedValue(access({ devices: [] }));
    renderWithProviders(<AccessCard />);
    expect(await screen.findByText("No paired devices.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Revoke all devices" })).not.toBeInTheDocument();
    expect(screen.getByText(/Tailscale or Cloudflare Tunnel/)).toBeInTheDocument();
  });
});
