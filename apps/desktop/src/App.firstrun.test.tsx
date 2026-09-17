import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { getConfig, getDoctor, getLocalRuntimes } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";

/**
 * The first-run gate asks "can this install run a model", not "does it hold a key".
 *
 * Measured on 2026-09-16: with `CHIMERA_DEFAULT_MODEL=ollama_chat/llama3` and no key, the gateway
 * served turns while this app opened the wizard demanding a key — the same question answered two
 * ways one screen apart. The doctor now answers it once (`can_answer`), and this is the app's half:
 * a keyless machine whose model runs locally goes straight in; a machine with neither still sees
 * the wizard, which is where a local model can be picked with one click.
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

function doctor(overrides: Partial<Awaited<ReturnType<typeof getDoctor>>>) {
  return {
    has_any_key: false,
    local_model: false,
    can_answer: false,
    configured_providers: [],
    default_model: "openrouter/x",
    tiers: { weak: "w", mid: "m", top: "t" },
    memory_backend: "json",
    cache: false,
    sandbox: "local",
    external_agents: [],
    editor: [],
    ...overrides,
  };
}

describe("App — the first-run gate", () => {
  beforeEach(async () => {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener() {},
      removeEventListener() {},
    })) as unknown as typeof window.matchMedia;
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
    vi.mocked(getLocalRuntimes).mockResolvedValue({ runtimes: [] } as never);
    for (const value of Object.values(await import("@/lib/api"))) {
      if (vi.isMockFunction(value) && !value.getMockImplementation()) {
        value.mockRejectedValue(new Error("not part of this test"));
      }
    }
  });

  it("shows the wizard to a machine with no key and no local model", async () => {
    vi.mocked(getDoctor).mockResolvedValue(doctor({}) as never);
    renderApp();

    await waitFor(() => expect(screen.getByText("Welcome to Chimera")).toBeInTheDocument());
  });

  it("lets a keyless machine whose model runs locally straight in", async () => {
    // `has_any_key` stays false — the wizard used to key on it alone — and `can_answer` is the
    // doctor's own answer, not something the client derives.
    vi.mocked(getDoctor).mockResolvedValue(
      doctor({ has_any_key: false, local_model: true, can_answer: true, default_model: "ollama_chat/llama3" }) as never,
    );
    renderApp();

    await waitFor(() => expect(getDoctor).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(/Starting Chimera/)).not.toBeInTheDocument());
    expect(screen.queryByText("Welcome to Chimera")).not.toBeInTheDocument();
    expect(screen.getAllByRole("navigation").length).toBeGreaterThan(0);
  });
});
