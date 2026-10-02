import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FloatWindow } from "@/components/shell/FloatWindow";
import { TooltipProvider } from "@/components/ui/tooltip";
import { FLOAT_CHANNEL, type FloatMessage } from "@/lib/float/protocol";
import { I18nProvider } from "@/lib/i18n";
import { STORAGE_KEY } from "@/lib/layout/store";
import { FakeChannel } from "@/test/fake-channel";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  listJobs: vi.fn(async () => ({ jobs: [] })),
}));

/**
 * Phase 7 of the dynamic screen, from the panel's own window: it draws one panel with the agent's state
 * the main window sends, and it goes — by its own button or when the main window takes the panel back —
 * saying so, so the panel is drawn in its dock again. It holds no layout.
 */
function mount(panel: "activity.tools" | "activity.jobs" = "activity.tools") {
  // The page's own stack (`main.tsx`), without the layout: this window must never write it.
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nProvider>
        <TooltipProvider>
          <FloatWindow panel={panel} />
        </TooltipProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

function listen() {
  const main = new FakeChannel(FLOAT_CHANNEL);
  const heard: FloatMessage[] = [];
  main.onmessage = (e) => heard.push(e.data as FloatMessage);
  return { main, heard };
}

describe("a panel in its own window", () => {
  let close: ReturnType<typeof vi.fn<() => void>>;

  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("BroadcastChannel", FakeChannel);
    close = vi.fn<() => void>();
    vi.spyOn(window, "close").mockImplementation(close);
  });

  afterEach(() => {
    FakeChannel.reset();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("draws the panel under its name, and asks the main window for the agent's state", async () => {
    const { heard } = listen();
    mount();

    expect(screen.getByRole("main", { name: "Tools" })).toBeInTheDocument();
    expect(document.title).toBe("Tools · Chimera");
    await waitFor(() => expect(heard).toContainEqual({ type: "hello", panel: "activity.tools" }));
  });

  it("draws the state the main window sends", async () => {
    const { main } = listen();
    mount();

    await act(async () => {
      main.postMessage({
        type: "agent",
        state: { status: "streaming", tools: [{ name: "read_file", ok: true }], report: null, busy: true },
      } satisfies FloatMessage);
      await Promise.resolve();
    });

    expect(await screen.findByText("read_file")).toBeInTheDocument();
  });

  it("closes itself when the main window takes its panel back, and not for another panel", async () => {
    const { main } = listen();
    mount("activity.tools");

    await act(async () => {
      main.postMessage({ type: "return", panel: "activity.jobs" } satisfies FloatMessage);
      await Promise.resolve();
    });
    expect(close).not.toHaveBeenCalled();

    await act(async () => {
      main.postMessage({ type: "return", panel: "activity.tools" } satisfies FloatMessage);
      await Promise.resolve();
    });
    expect(close).toHaveBeenCalled();
  });

  it("says it closed and closes, from its own button", async () => {
    const { heard } = listen();
    mount();

    await userEvent.click(screen.getByRole("button", { name: "Bring back" }));

    await waitFor(() => expect(heard).toContainEqual({ type: "closed", panel: "activity.tools" }));
    expect(close).toHaveBeenCalled();
  });

  it("says it closed when the window goes some other way", async () => {
    const { heard } = listen();
    mount();

    window.dispatchEvent(new Event("pagehide"));

    await waitFor(() => expect(heard).toContainEqual({ type: "closed", panel: "activity.tools" }));
  });

  it("never writes the layout, which belongs to the main window", () => {
    mount();

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
