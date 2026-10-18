import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  browseDirs,
  getCodeSession,
  getConfig,
  getDoctor,
  getFsTree,
  listArchivedCodeSessions,
  listCodeSessions,
  listShares,
  listWorks,
} from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";
import { LayoutProvider } from "@/lib/layout/context";
import { WORKSPACE_KEY, readLastSession } from "@/lib/workspace";

/**
 * C7 of study 34. DESIGN.md and the comment above the palette's commands in `App.tsx` both said ⌘K
 * reaches "every conversation by title", and the list held no conversation at all: typing a
 * conversation's name found nothing, which reads as "no such conversation". Now the recent ones are
 * listed with their project, a search reaches every one and the archived ones (marked), and choosing
 * one opens it where a click on its sidebar row would.
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
            {/* `main.tsx` puts the layout provider here; App reads it for the palette. */}
            <LayoutProvider>
              <App />
            </LayoutProvider>
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
    local_model: false,
    can_answer: true,
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


const LIVE = {
  id: "s-live",
  title: "Fix the parser",
  workspace: "/projects/agent",
  turns: 2,
  updated_at: 200,
  project: "/projects/agent",
  project_name: "agent",
};
const ARCHIVED = {
  id: "s-old",
  title: "Old spike on caching",
  workspace: "/projects/vps",
  turns: 1,
  updated_at: 100,
};

beforeEach(() => {
  vi.mocked(listCodeSessions).mockResolvedValue([LIVE] as never);
  vi.mocked(listArchivedCodeSessions).mockResolvedValue([ARCHIVED] as never);
  vi.mocked(getCodeSession).mockResolvedValue({ id: "x", title: "", workspace: "", turns: [] } as never);
  // What an opened conversation asks for besides its transcript.
  vi.mocked(listShares).mockResolvedValue({ shares: [] } as never);
  vi.mocked(listWorks).mockResolvedValue({ works: [] } as never);
});

const LENTO = { timeout: 5000 };

async function openPalette() {
  const user = userEvent.setup();
  // The shell being up is the readiness signal. It was `getConfig` having been called, which was
  // true only because the conversation's status bar reads the config — and the editor screen leaves
  // that bar out while the agent is idle (it has its own), so the probe waited on a request the
  // screen under test never makes.
  await screen.findAllByRole("main");
  await user.keyboard("{Control>}k{/Control}");
  const input = await screen.findByRole("combobox");
  return { user, input };
}

describe("the command palette's conversations", () => {
  it("lists a recent conversation with its project before anything is typed, and not the archived one", async () => {
    renderApp();
    await openPalette();
    const list = screen.getByRole("listbox");
    expect(await within(list).findByRole("option", { name: /Fix the parser.*agent/ }, LENTO)).toBeInTheDocument();
    expect(within(list).queryByRole("option", { name: /Old spike/ })).toBeNull();
  });

  it("reaches an archived conversation by search, marked archived", async () => {
    renderApp();
    const { user, input } = await openPalette();
    await within(screen.getByRole("listbox")).findByRole("option", { name: /Fix the parser/ }, LENTO);
    await user.type(input, "spike");
    expect(
      await screen.findByRole("option", { name: /Old spike on caching.*Archived · vps/ }, LENTO),
    ).toBeInTheDocument();
  });

  it("opens the chosen conversation on the conversation screen", async () => {
    renderApp();
    const { user, input } = await openPalette();
    await within(screen.getByRole("listbox")).findByRole("option", { name: /Fix the parser/ }, LENTO);
    await user.type(input, "parser");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(vi.mocked(getCodeSession)).toHaveBeenCalledWith("s-live"), LENTO);
    expect(localStorage.getItem(WORKSPACE_KEY)).toBe("/projects/agent");
    expect(readLastSession("/projects/agent")).toBe("s-live");
  });

  it("opens it from another screen too, by going to the conversation screen", async () => {
    window.location.hash = "#/edit";
    renderApp();
    const { user, input } = await openPalette();
    await within(screen.getByRole("listbox")).findByRole("option", { name: /Fix the parser/ }, LENTO);
    await user.type(input, "parser");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(window.location.hash).toBe("#/code"), LENTO);
    await waitFor(() => expect(vi.mocked(getCodeSession)).toHaveBeenCalledWith("s-live"), LENTO);
  });
});
