/** Full control through the bridge on screen (owner's decision, 2026-10-18).
 *
 * The card on the Security tab is the only door that turns it on, and it asks first; the bar on top
 * of every screen says it is on and ends it in one click; and while it is on, the screen opens what
 * the bridge asks it to open — only the app's own screens, never anything else.
 */

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BridgeFullControlCard } from "@/components/BridgeFullControlCard";
import { FullControlBanner } from "@/components/FullControlBanner";
import { getBridgeFullControl, setBridgeFullControl, streamUiEvents } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getBridgeFullControl: vi.fn(),
  setBridgeFullControl: vi.fn(),
  streamUiEvents: vi.fn(),
}));

function state(over: Record<string, unknown> = {}) {
  return { on: false, available: true, active: false, actions: [], ...over } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getBridgeFullControl).mockResolvedValue(state());
  vi.mocked(setBridgeFullControl).mockImplementation(async (on: boolean) =>
    state({ on, active: on }),
  );
  // A stream that stays open until the test ends: the banner listens and nothing arrives.
  vi.mocked(streamUiEvents).mockImplementation(
    (_since, _onNavigate, _onSeq, signal) =>
      new Promise((resolve) => signal?.addEventListener("abort", () => resolve(null))),
  );
});

describe("the full-control card", () => {
  it("asks before turning it on, and Cancel changes nothing", async () => {
    const user = userEvent.setup();
    renderWithProviders(<BridgeFullControlCard />);
    const toggle = await screen.findByRole("switch", { name: "Full control through the bridge" });
    await user.click(toggle);
    expect(await screen.findByText("Give Claude full control of this app?")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(setBridgeFullControl).not.toHaveBeenCalled();

    await user.click(toggle);
    await user.click(await screen.findByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(setBridgeFullControl).toHaveBeenCalledWith(true));
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
  });

  it("turns it off in one click, with no dialog", async () => {
    vi.mocked(getBridgeFullControl).mockResolvedValue(state({ on: true, active: true }));
    const user = userEvent.setup();
    renderWithProviders(<BridgeFullControlCard />);
    await user.click(await screen.findByRole("switch", { name: "Full control through the bridge" }));
    await waitFor(() => expect(setBridgeFullControl).toHaveBeenCalledWith(false));
    expect(screen.queryByText("Give Claude full control of this app?")).not.toBeInTheDocument();
  });

  it("cannot be turned on before the two switches it needs", async () => {
    vi.mocked(getBridgeFullControl).mockResolvedValue(state({ available: false }));
    renderWithProviders(<BridgeFullControlCard />);
    const toggle = await screen.findByRole("switch", { name: "Full control through the bridge" });
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/Turn on "Allow Claude to operate this app"/)).toBeInTheDocument();
  });

  it("lists what was done with it: route and ids, never content", async () => {
    vi.mocked(getBridgeFullControl).mockResolvedValue(
      state({
        on: true,
        actions: [
          { event: "bridge.full_control", at: "2026-10-18T12:00:00+00:00", route: "design.delete",
            status: 200, ids: { design_id: "d-123" }, on: null, via: "bridge" },
          { event: "bridge.full_control.switch", at: "2026-10-18T11:59:00+00:00", route: "",
            status: null, ids: {}, on: true, via: "screen" },
        ],
      }),
    );
    renderWithProviders(<BridgeFullControlCard />);
    const rows = await screen.findAllByTestId("full-control-action");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("design.delete");
    expect(rows[0]).toHaveTextContent("design_id=d-123");
    expect(rows[0]).toHaveTextContent("status 200");
    expect(rows[1]).toHaveTextContent("Switched on");
    expect(screen.getByText("Recent actions (2)")).toBeInTheDocument();
  });
});

describe("the full-control bar", () => {
  it("is not there while full control is off", async () => {
    renderWithProviders(<FullControlBanner navigate={vi.fn()} />);
    await waitFor(() => expect(getBridgeFullControl).toHaveBeenCalled());
    expect(screen.queryByTestId("full-control-banner")).not.toBeInTheDocument();
    expect(streamUiEvents).not.toHaveBeenCalled();
  });

  it("says Claude has full control and ends it in one click", async () => {
    vi.mocked(getBridgeFullControl).mockResolvedValue(state({ on: true, active: true }));
    const user = userEvent.setup();
    renderWithProviders(<FullControlBanner navigate={vi.fn()} />);
    expect(
      await screen.findByText("Claude has full control of this app through the bridge."),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Turn off" }));
    await waitFor(() => expect(setBridgeFullControl).toHaveBeenCalledWith(false));
    await waitFor(() =>
      expect(screen.queryByTestId("full-control-banner")).not.toBeInTheDocument(),
    );
  });

  it("opens the app's own screens when the bridge asks, and nothing else", async () => {
    vi.mocked(getBridgeFullControl).mockResolvedValue(state({ on: true, active: true }));
    vi.mocked(streamUiEvents).mockImplementation(async (_since, onNavigate, onSeq, signal) => {
      onSeq(1);
      onNavigate({ view: "settings", params: { tab: "security" }, seq: 2 });
      onNavigate({ view: "nowhere", params: {}, seq: 3 });
      onNavigate({ view: "maturity", params: {}, seq: 4 });
      return new Promise((resolve) => signal?.addEventListener("abort", () => resolve(null)));
    });
    const navigate = vi.fn();
    renderWithProviders(<FullControlBanner navigate={navigate} />);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("settings", { tab: "security" }));
    expect(navigate).toHaveBeenCalledTimes(1);
    // It starts from "only what comes next": nothing asked before the screen listened is replayed.
    expect(vi.mocked(streamUiEvents).mock.calls[0][0]).toBe(-1);
  });
});
