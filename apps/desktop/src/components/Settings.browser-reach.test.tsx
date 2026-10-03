import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging, patchConfig } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getOllamaModels: vi.fn(async () => ({
    base_url: "",
    reachable: false,
    models: [],
    reason: "no_url",
  })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/**
 * Where the agent's browser may go (study 29, P5.2): a site list that only narrows, and loopback
 * ports the owner declares so the agent can look at the app it is changing. Both are switches the
 * owner decided must be changeable from the app, so the test that matters is that each row shows
 * what it holds and writes its own key — never the other one, and never a broader setting.
 */
function config(browser: Record<string, unknown> | undefined) {
  return {
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
    applies: {
      CHIMERA_BROWSER_SITES: "next_conversation",
      CHIMERA_BROWSER_LOCAL_PORTS: "next_conversation",
    },
    ...(browser === undefined ? {} : { browser }),
  };
}

describe("Settings — where the browser may go", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true,
      local_model: false,
      can_answer: true,
      configured_providers: ["openrouter"],
      default_model: "openrouter/x",
      tiers: { weak: "a", mid: "b", top: "c" },
      memory_backend: "json",
      cache: false,
      sandbox: "local",
    } as never);
    vi.mocked(getInstructions).mockResolvedValue({ text: "", path: "" } as never);
    vi.mocked(getMessaging).mockResolvedValue({ running: false, channels: [] } as never);
  });

  it("shows both lists as written", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ headless: true, sites: ["github.com", "*.github.com"], local_ports: [3000, 5173] }) as never,
    );
    renderWithProviders(<Settings />);
    expect(
      await screen.findByRole("textbox", { name: "Sites the browser opens without asking" }),
    ).toHaveValue("github.com, *.github.com");
    expect(screen.getByRole("textbox", { name: "Local ports the browser may open" })).toHaveValue(
      "3000, 5173",
    );
  });

  it("reads as empty when the server does not send the lists", async () => {
    // A server one release behind sends `browser: {headless}` only; empty is the shipped browser.
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true }) as never);
    renderWithProviders(<Settings />);
    expect(
      await screen.findByRole("textbox", { name: "Sites the browser opens without asking" }),
    ).toHaveValue("");
    expect(screen.getByRole("textbox", { name: "Local ports the browser may open" })).toHaveValue("");
  });

  it("writes each list to its own key", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ headless: true, sites: [], local_ports: [] }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const ports = await screen.findByRole("textbox", { name: "Local ports the browser may open" });
    await user.type(ports, "3000");
    await user.click(within(ports.parentElement as HTMLElement).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledTimes(1));
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_BROWSER_LOCAL_PORTS: "3000" });

    const sites = screen.getByRole("textbox", { name: "Sites the browser opens without asking" });
    await user.type(sites, "github.com");
    await user.click(within(sites.parentElement as HTMLElement).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledTimes(2));
    expect(vi.mocked(patchConfig).mock.calls[1][0]).toEqual({ CHIMERA_BROWSER_SITES: "github.com" });
  });
});
