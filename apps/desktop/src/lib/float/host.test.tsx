import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Dock, LayoutDnd } from "@/components/shell/Dock";
import { HiddenTray } from "@/components/shell/HiddenTray";
import { MaximizedPanel } from "@/components/shell/Maximize";
import { ToastProvider } from "@/components/ui/toast";
import { AgentProvider, useAgent } from "@/lib/agent-context";
import { FLOAT_POLL_MS, FloatHost, useFloat } from "@/lib/float/host";
import { FLOAT_CHANNEL, type FloatMessage } from "@/lib/float/protocol";
import { useLayout } from "@/lib/layout/context";
import { STORAGE_KEY } from "@/lib/layout/store";
import { FakeChannel } from "@/test/fake-channel";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));
vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getResources: vi.fn(async () => {
    throw new Error("not in this test");
  }),
  listJobs: vi.fn(async () => ({ jobs: [] })),
}));

/**
 * Phase 7 of the dynamic screen, from the main window: a panel opens in a window of its own, leaves its
 * dock while it is there, and comes back however that window goes — closed from the tray, closed by
 * itself, or gone without a word. Which panels float is state of the run, never of the layout.
 */
interface FakeWindow {
  closed: boolean;
  close: ReturnType<typeof vi.fn>;
}

let opened: { url: string; name: string; features: string; win: FakeWindow }[] = [];
let refuse = false;
let api: { float: ReturnType<typeof useFloat>; agent: ReturnType<typeof useAgent>; layout: ReturnType<typeof useLayout> } | null =
  null;

function Probe() {
  api = { float: useFloat(), agent: useAgent(), layout: useLayout() };
  return null;
}

function mount() {
  return renderWithProviders(
    <ToastProvider>
      <AgentProvider value={{ tools: [{ name: "read_file", ok: true }] }}>
        <FloatHost>
          <LayoutDnd>
            <Probe />
            <Dock zone="right" />
            <MaximizedPanel />
            <HiddenTray />
          </LayoutDnd>
        </FloatHost>
      </AgentProvider>
    </ToastProvider>,
  );
}

const rightDock = () => screen.getByRole("region", { name: "Right panel" });

describe("floating panels, from the main window", () => {
  beforeEach(() => {
    localStorage.clear();
    opened = [];
    refuse = false;
    api = null;
    vi.stubGlobal("BroadcastChannel", FakeChannel);
    vi.spyOn(window, "open").mockImplementation((url, name, features) => {
      if (refuse) return null;
      const win: FakeWindow = { closed: false, close: vi.fn(() => void (win.closed = true)) };
      opened.push({ url: String(url), name: String(name), features: String(features), win });
      return win as unknown as Window;
    });
  });

  afterEach(() => {
    FakeChannel.reset();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("opens the panel's window at its address from the move menu, and the panel leaves its dock", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(within(rightDock()).getByRole("button", { name: "Move Tools" }));
    await user.click(await screen.findByRole("menuitem", { name: "To a window of its own" }));

    expect(opened).toHaveLength(1);
    expect(opened[0].url).toBe(`${window.location.origin}/?float=activity.tools`);
    expect(opened[0].name).toBe("chimera-float-activity-tools");
    expect(within(rightDock()).queryByRole("region", { name: "Tools" })).not.toBeInTheDocument();
    // Still reachable: the tray lists it, under a label that says where it went.
    expect(screen.getByRole("button", { name: "Hidden or in a window: 1" })).toBeInTheDocument();
  });

  it("leaves the panel where it was when the window does not open, and says so", async () => {
    const user = userEvent.setup();
    refuse = true;
    mount();

    await user.click(within(rightDock()).getByRole("button", { name: "Move Tools" }));
    await user.click(await screen.findByRole("menuitem", { name: "To a window of its own" }));

    expect(within(rightDock()).getByRole("region", { name: "Tools" })).toBeInTheDocument();
    expect(await screen.findByText("The window for Tools did not open; the panel stays where it was.")).toBeInTheDocument();
    expect(api?.float.floating.size).toBe(0);
  });

  it("brings the panel back from the tray, closing its window", async () => {
    const user = userEvent.setup();
    mount();
    act(() => void api?.float.popOut("activity.jobs"));

    await user.click(screen.getByRole("button", { name: "Hidden or in a window: 1" }));
    await user.click(await screen.findByRole("menuitem", { name: /Background jobs, in a window/ }));

    expect(opened[0].win.close).toHaveBeenCalled();
    expect(api?.float.floating.size).toBe(0);
    expect(screen.queryByRole("button", { name: /Hidden/ })).not.toBeInTheDocument();
  });

  it("tells the window its panel was taken back, so a window it cannot close closes itself", async () => {
    mount();
    const other = new FakeChannel(FLOAT_CHANNEL);
    const heard: FloatMessage[] = [];
    other.onmessage = (e) => heard.push(e.data as FloatMessage);
    act(() => void api?.float.popOut("activity.tools"));

    act(() => api?.float.bringBack("activity.tools"));

    await waitFor(() => expect(heard).toContainEqual({ type: "return", panel: "activity.tools" }));
  });

  it("draws the panel again when its window says it closed", async () => {
    mount();
    act(() => void api?.float.popOut("activity.tools"));
    expect(within(rightDock()).queryByRole("region", { name: "Tools" })).not.toBeInTheDocument();

    const other = new FakeChannel(FLOAT_CHANNEL);
    await act(async () => {
      other.postMessage({ type: "closed", panel: "activity.tools" } satisfies FloatMessage);
      await Promise.resolve();
    });

    expect(within(rightDock()).getByRole("region", { name: "Tools" })).toBeInTheDocument();
  });

  it("draws the panel again when its window went without a word", async () => {
    mount();
    act(() => void api?.float.popOut("activity.tools"));

    opened[0].win.closed = true;

    await waitFor(() => expect(within(rightDock()).getByRole("region", { name: "Tools" })).toBeInTheDocument(), {
      timeout: FLOAT_POLL_MS * 3,
    });
  });

  it("answers a new window with the agent's state, and sends each change after", async () => {
    mount();
    act(() => void api?.float.popOut("activity.tools"));
    const other = new FakeChannel(FLOAT_CHANNEL);
    const heard: FloatMessage[] = [];
    other.onmessage = (e) => heard.push(e.data as FloatMessage);

    other.postMessage({ type: "hello", panel: "activity.tools" } satisfies FloatMessage);
    await waitFor(() =>
      expect(heard.some((m) => m.type === "agent" && m.state.tools[0]?.name === "read_file")).toBe(true),
    );

    act(() => api?.agent.publish({ tools: [{ name: "write_file", ok: false }] }));
    await waitFor(() =>
      expect(heard.some((m) => m.type === "agent" && m.state.tools[0]?.name === "write_file")).toBe(true),
    );
  });

  it("stores nothing about a window: the layout is the same before and after", () => {
    mount();
    const before = localStorage.getItem(STORAGE_KEY);
    const layout = api?.layout.layout;

    act(() => void api?.float.popOut("activity.jobs"));

    expect(api?.layout.layout).toBe(layout);
    expect(localStorage.getItem(STORAGE_KEY)).toBe(before);
    expect(api?.layout.canUndo).toBe(false);
  });

  it("stops filling the main area with a panel that goes to a window", () => {
    mount();
    act(() => void api?.layout.dispatch({ type: "maximize", panel: "activity.tools" }));

    act(() => void api?.float.popOut("activity.tools"));

    expect(api?.layout.layout.maximized).toBeNull();
  });

  it("closes the windows when the main one goes, so no panel is left drawn by nothing", () => {
    mount();
    act(() => void api?.float.popOut("activity.tools"));
    act(() => void api?.float.popOut("activity.jobs"));

    window.dispatchEvent(new Event("pagehide"));

    expect(opened.every((o) => o.win.close.mock.calls.length > 0)).toBe(true);
  });
});

describe("without the host", () => {
  it("offers no window, and draws every panel in its dock", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <AgentProvider>
        <LayoutDnd>
          <Dock zone="right" />
        </LayoutDnd>
      </AgentProvider>,
    );

    await user.click(within(rightDock()).getByRole("button", { name: "Move Tools" }));
    await screen.findByRole("menuitem", { name: "To the bottom dock" });

    expect(screen.queryByRole("menuitem", { name: "To a window of its own" })).not.toBeInTheDocument();
  });
});
