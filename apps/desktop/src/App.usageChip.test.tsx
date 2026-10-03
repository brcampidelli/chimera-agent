import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import { getConfig, getDoctor, getLocalRuntimes } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";
import { LayoutProvider } from "@/lib/layout/context";

/**
 * The cost chip in the status bar said it took you to Usage, and it took you to General.
 *
 * Usage moved into Settings as a tab, and the chip kept calling `navigate("settings")`. The tab was
 * `useState` inside the screen, so there was no way to name it from outside: every click landed on
 * the first tab, under a comment saying "the cost chip still takes you straight there".
 *
 * The real chip renders only after a chat turn has produced a cost report, which needs the whole
 * agent stream. What is under test here is App's half — what the chip's callback does — so the bar
 * is replaced by a button that calls the very callback App hands it. The bar's own wiring (the chip
 * calls `onOpenUsage`) is `shell.test.tsx`'s job.
 */
vi.mock("@/components/shell/AgentStatusBar", () => ({
  AgentStatusBar: ({ onOpenUsage }: { onOpenUsage?: () => void }) => (
    <button type="button" onClick={onOpenUsage}>
      cost chip
    </button>
  ),
}));

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

describe("App — the cost chip opens Usage", () => {
  beforeEach(async () => {
    window.location.hash = "";
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener() {},
      removeEventListener() {},
    })) as unknown as typeof window.matchMedia;
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
    vi.mocked(getDoctor).mockResolvedValue(DOCTOR as never);
    vi.mocked(getLocalRuntimes).mockResolvedValue({ runtimes: [] } as never);
    for (const value of Object.values(await import("@/lib/api"))) {
      if (vi.isMockFunction(value) && !value.getMockImplementation()) {
        value.mockRejectedValue(new Error("not part of this test"));
      }
    }
  });

  it("lands on the Usage tab, not General", async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(await screen.findByRole("button", { name: "cost chip" }));

    const usage = await screen.findByRole("tab", { name: "Usage" });
    expect(usage).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("aria-selected", "false");
    expect(window.location.hash).toBe("#/settings?tab=usage");
  });

  it("still lands on Usage when Settings is already open on another tab", async () => {
    // The case a remount-on-navigate fix would miss: the screen is mounted, its tab is General,
    // and the URL already says `settings`. The chip has to move the tab, not just the screen.
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("button", { name: "cost chip" }));
    await user.click(await screen.findByRole("tab", { name: "General" }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "General" })).toHaveAttribute("aria-selected", "true"),
    );

    await user.click(screen.getByRole("button", { name: "cost chip" }));

    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Usage" })).toHaveAttribute("aria-selected", "true"),
    );
  });
});
