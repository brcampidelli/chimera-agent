import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App, { HEARTBEAT_MS } from "@/App";
import { PATIENCE_MS } from "@/components/BackendDown";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { getConfig, getDoctor } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";

/**
 * `main.tsx`'s stack, not `test/utils`'s.
 *
 * The shared helper leaves out the tooltip and toast providers, which nothing under it has ever
 * needed — mounting the whole App does, and it fails at render with a Radix context error rather
 * than anything to do with what is being tested. Retries are off so a rejected call surfaces at
 * once instead of being retried through the timers this file is driving by hand.
 */
function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <TooltipProvider>
          <ToastProvider>
            <App />
          </ToastProvider>
        </TooltipProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

/**
 * Every call in `@/lib/api` becomes a mock, and then behaves like the backend this test is
 * simulating: while it is "down", everything rejects, because that is what a dead sidecar does to
 * every request in the window, not only to the one the app happens to be watching.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  const stubbed: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(actual)) {
    stubbed[name] = typeof value === "function" ? vi.fn() : value;
  }
  return stubbed;
});

/** Whether the backend is answering. Flipped by the tests; read by every stub. */
let up = true;

function answering<T>(value: T) {
  return () =>
    up ? Promise.resolve(value) : Promise.reject(new Error("the backend is not answering"));
}

const CONFIG = {
  models: {
    default: "openrouter/x",
    weak: "",
    mid: "",
    orchestrator: "",
    cost_mode: "auto",
    cascade: false,
    api_base: null,
    fallback_models: [],
    tiers: { weak: "a", mid: "b", top: "c" },
  },
  memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
  cache: { completion: false, prompt: false },
  autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
  sandbox: { mode: "local", image: "python:3.12-slim" },
  server: { token_set: false },
  mcp: { autoload: false },
  automation: { cron: true },
  guard: { chat: false },
  providers: [],
};

const doctorSays = answering({
  has_any_key: true,
  configured_providers: ["openrouter"],
  default_model: "m",
  tiers: { weak: "w", mid: "m", top: "t" },
  memory_backend: "sqlite",
  cache: true,
  sandbox: "local",
  external_agents: [],
  editor: [],
});

/**
 * The app reconnects, and says so while it has not.
 *
 * The native shell restarts a backend that died (see `src-tauri/src/main.rs`); this side is the
 * half the user actually sees. Before it, the window kept running with a dead server behind it:
 * every panel offered a "Try again" that could only fail, nothing said why, and the only way out
 * was to guess that closing and reopening the app would fix it.
 *
 * Fake timers with `shouldAdvanceTime`, because the whole feature IS a timer: the heartbeat has to
 * be driven forward to reach the failure, and Testing Library's own waiting needs the clock to keep
 * moving while it does.
 */
describe("App — when the backend stops answering", () => {
  beforeEach(async () => {
    // A plain function, not a spy: `restoreMocks` in vite.config wipes the `vi.fn()` stub that
    // `test/setup.ts` installs, and the theme layer calls `matchMedia` during App's first render —
    // so a spy here would be reset out from under the very first test.
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener() {},
      removeEventListener() {},
    })) as unknown as typeof window.matchMedia;
    vi.useFakeTimers({ shouldAdvanceTime: true });
    up = true;
    vi.mocked(getDoctor).mockImplementation(doctorSays);
    // One real screen query, used below as the probe for "did the app refresh after reconnecting".
    // `as never` is this suite's existing idiom for a fixture that stands in for a wide response
    // type (see Settings.applies.test.tsx) — nothing here reads past `autonomy`.
    vi.mocked(getConfig).mockImplementation(answering(CONFIG as never));
    // Everything else fails whatever happens: no screen's data is what is being asked about here,
    // and a stub that resolved `undefined` would fail as a broken test rather than a dead backend.
    for (const value of Object.values(await import("@/lib/api"))) {
      if (vi.isMockFunction(value) && !value.getMockImplementation()) {
        value.mockRejectedValue(new Error("not part of this test"));
      }
    }
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Render, and wait until the first heartbeat has landed. */
  async function launch() {
    renderApp();
    await waitFor(() => expect(getDoctor).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(/Starting Chimera/)).not.toBeInTheDocument());
  }

  /** Take the backend away and let the heartbeat notice. */
  async function pullThePlug() {
    up = false;
    await vi.advanceTimersByTimeAsync(HEARTBEAT_MS * 2);
  }

  it("says the backend is down, instead of leaving the screens to fail one by one", async () => {
    await launch();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    await pullThePlug();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/backend stopped responding/i);
    // And it says what is being done about it, not only that something is wrong.
    expect(alert).toHaveTextContent(/starting it again/i);
  });

  it("keeps the app on screen rather than replacing it with an error page", async () => {
    // The outage lasts about two seconds in the normal case. Unmounting the app over it would
    // throw away a half-written message and every open editor tab to say so.
    await launch();
    const railBefore = screen.getAllByRole("navigation").length;

    await pullThePlug();
    await screen.findByRole("alert");

    expect(screen.getAllByRole("navigation")).toHaveLength(railBefore);
  });

  it("tells the user to close and reopen it once restarting has clearly not worked", async () => {
    await launch();
    await pullThePlug();
    const alert = await screen.findByRole("alert");
    expect(alert).not.toHaveTextContent(/Close Chimera and open it again/i);

    await vi.advanceTimersByTimeAsync(PATIENCE_MS + 1_000);

    // The shell's fuse has stopped trying by now and this side cannot see that, so the honest thing
    // left to say is the thing the user would otherwise have to guess.
    expect(await screen.findByRole("alert")).toHaveTextContent(/Close Chimera and open it again/i);
  });

  it("takes the notice away when the backend comes back", async () => {
    await launch();
    await pullThePlug();
    await screen.findByRole("alert");

    up = true;
    await vi.advanceTimersByTimeAsync(HEARTBEAT_MS * 2);

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("refreshes the screens that failed during the outage, instead of leaving them broken", async () => {
    // The half that makes reconnecting mean something. React Query does not retry a query that
    // already failed because a different one succeeded, so without the recovery the heartbeat goes
    // green, the notice disappears, and every panel underneath still shows its own dead "Try
    // again" until the user clicks each one.
    await launch();
    await waitFor(() => expect(getConfig).toHaveBeenCalled());
    await pullThePlug();
    await screen.findByRole("alert");
    const asked = vi.mocked(getConfig).mock.calls.length;

    up = true;
    await vi.advanceTimersByTimeAsync(HEARTBEAT_MS * 2);
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());

    await waitFor(() =>
      expect(vi.mocked(getConfig).mock.calls.length).toBeGreaterThan(asked),
    );
  });
});
