import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { getConfig, getDoctor, browseDirs, getFsTree } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";
import { WORKSPACE_KEY } from "@/lib/workspace";

/**
 * One app, one current project.
 *
 * The comment above `editWorkspace` in App.tsx has claimed that since the editor was added, and
 * the code did not deliver it: the initialiser runs once, at startup, while the chat screen writes
 * the stored value on every project switch. Choosing a project in Code and opening the editor
 * therefore showed the folder that had been open when the app launched — the file tree listing one
 * project while the chat edited another, with nothing on screen admitting the two disagreed.
 *
 * Found by using the app, not by reading it.
 */
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  const stubbed: Record<string, unknown> = {};
  for (const [name, value] of Object.entries(actual)) {
    stubbed[name] = typeof value === "function" ? vi.fn() : value;
  }
  return stubbed;
});

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

beforeEach(() => {
  // A plain function, not a spy: `restoreMocks` wipes the stub `test/setup.ts` installs, and the
  // theme layer calls `matchMedia` during App's first render.
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    addEventListener() {},
    removeEventListener() {},
  })) as unknown as typeof window.matchMedia;
  vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
  vi.mocked(getDoctor).mockResolvedValue({
    has_any_key: true,
    configured_providers: ["openrouter"],
    default_model: "m",
    tiers: { weak: "w", mid: "m", top: "t" },
    memory_backend: "sqlite",
    cache: true,
    sandbox: "local",
    external_agents: [],
    editor: [],
  } as never);
  vi.mocked(browseDirs).mockResolvedValue({ path: "", parent: null, dirs: [] } as never);
  vi.mocked(getFsTree).mockResolvedValue({
    workspace: "",
    path: "",
    entries: [],
    capped: false,
  } as never);
  localStorage.clear();
  window.location.hash = "";
});

afterEach(() => {
  vi.clearAllMocks();
  window.location.hash = "";
});

/** The default one second is not enough on a loaded machine.
 *
 * These assertions are about ORDER — the LAST tree call must be the project chosen most
 * recently — and they went from always green to mostly green purely because the suite grew:
 * one failed inside a full run and passed alone and on the next run. So the clock is raised
 * rather than the bar lowered; the last call still has to be the right project.
 */
const LENTO = { timeout: 5000 };

describe("the project the editor works in", () => {
  it("is the one the chat screen last chose, not the one open at startup", async () => {
    // The app starts with one project…
    localStorage.setItem(WORKSPACE_KEY, "/projects/first");
    renderApp();
    await waitFor(() => expect(vi.mocked(getConfig)).toHaveBeenCalled());

    // …the chat screen switches to another, which is exactly what `writeWorkspace` does…
    localStorage.setItem(WORKSPACE_KEY, "/projects/second");

    // …and opening the editor must show the second one.
    window.location.hash = "#/edit";
    window.dispatchEvent(new HashChangeEvent("hashchange"));

    await waitFor(
      () => {
        const shown = vi
          .mocked(getFsTree)
          .mock.calls.map((call) => String(call[0]))
          .filter((ws) => ws.startsWith("/projects/"));
        expect(shown[shown.length - 1]).toBe("/projects/second");
      },
      LENTO,
    );
  });

  it("survives leaving the editor and coming back", async () => {
    localStorage.setItem(WORKSPACE_KEY, "/projects/second");
    renderApp();
    await waitFor(() => expect(vi.mocked(getConfig)).toHaveBeenCalled());

    for (const hash of ["#/edit", "#/code", "#/edit"]) {
      window.location.hash = hash;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    }

    // Re-reading on the way in must not mean re-reading something stale: nothing wrote to storage
    // in between, so the folder is the same one both times.
    await waitFor(
      () => {
        const shown = vi
          .mocked(getFsTree)
          .mock.calls.map((call) => String(call[0]))
          .filter((ws) => ws.startsWith("/projects/"));
        expect(shown[shown.length - 1]).toBe("/projects/second");
      },
      LENTO,
    );
  });
});
