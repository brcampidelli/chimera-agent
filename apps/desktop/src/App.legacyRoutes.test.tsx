import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { getConfig, getDoctor, getLocalRuntimes, getMemoryLayers, getMemoryProfile, getInstructions, listCodeProjects } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";
import { LayoutProvider } from "@/lib/layout/context";

/**
 * Skills moved from Knowledge to Settings › Personalization, and MCP and OpenAPI from Connections to
 * Connectors (study 04, §1.3). The old addresses are in bookmarks and in what the agent has written,
 * so App rewrites them — in place, so the back button does not return to an entry that would only be
 * rewritten again. `legacyRoutes.test.ts` covers each row of the table; this is App's half.
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

const DOCTOR = {
  has_any_key: true,
  local_model: false,
  can_answer: true,
  configured_providers: ["openrouter"],
  default_model: "openrouter/x",
  tiers: { weak: "w", mid: "m", top: "t" },
  memory_backend: "json",
  cache: false,
  sandbox: "local",
  external_agents: [],
  editor: [],
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
            <LayoutProvider>
              <App />
            </LayoutProvider>
          </ToastProvider>
        </TooltipProvider>
      </I18nProvider>
    </QueryClientProvider>,
  );
}

describe("App — moved addresses", () => {
  beforeEach(async () => {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener() {},
      removeEventListener() {},
    })) as unknown as typeof window.matchMedia;
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
    vi.mocked(getDoctor).mockResolvedValue(DOCTOR as never);
    vi.mocked(getLocalRuntimes).mockResolvedValue({ runtimes: [] } as never);
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "English", instructions: "" } as never);
    vi.mocked(getMemoryProfile).mockResolvedValue({ profile: "", persona: [] } as never);
    vi.mocked(getMemoryLayers).mockResolvedValue({ total: 0, clean: 0, tainted: 0, layers: [], by_source: [], semantic_embeddings_enabled: false } as never);
    vi.mocked(listCodeProjects).mockResolvedValue([] as never);
    for (const value of Object.values(await import("@/lib/api"))) {
      if (vi.isMockFunction(value) && !value.getMockImplementation()) {
        value.mockRejectedValue(new Error("not part of this test"));
      }
    }
  });

  it("keeps the direct Knowledge › Profile address on Profile", async () => {
    window.location.hash = "#/knowledge?tab=profile";
    renderApp();

    await waitFor(() => expect(window.location.hash).toBe("#/knowledge?tab=profile"));
    expect(await screen.findByRole("heading", { name: "Facts stored about you" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Profile" })).toHaveAttribute("aria-selected", "true");
  });

  it("opens Settings › Skills for the old Knowledge › Skills address, replacing it", async () => {
    window.location.hash = "#/knowledge?tab=skills";
    const before = window.history.length;
    renderApp();

    await waitFor(() => expect(window.location.hash).toBe("#/settings?tab=skills"));
    expect(await screen.findByRole("tab", { name: "Skills" })).toHaveAttribute("aria-selected", "true");
    // Replaced, not pushed: Back must not land on the old address and bounce forward again.
    expect(window.history.length).toBe(before);
  });

  it("carries the old section anchor into the new address", async () => {
    window.location.hash = "#/knowledge?tab=skills#skills-catalog";
    renderApp();
    await waitFor(() => expect(window.location.hash).toBe("#/settings?tab=skills&view=discover"));
  });

  it("opens Connectors for a hand-typed MCP address", async () => {
    window.location.hash = "#/settings?tab=mcp";
    renderApp();
    await waitFor(() => expect(window.location.hash).toBe("#/settings?tab=connectors"));
    expect(await screen.findByRole("tab", { name: "Connectors" })).toHaveAttribute("aria-selected", "true");
  });
});
