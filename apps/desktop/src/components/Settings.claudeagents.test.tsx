import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging, patchConfig } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDeferSaving: vi.fn(async () => ({ builtin: null, mcp: null, mcp_state: "autoload_off" })),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getBrowserLink: vi.fn(async () => ({ paired: false, connected: false })),
  getBrowserTrustedSites: vi.fn(async () => ({ trusted_sites: [] })),
  stopBrowserLink: vi.fn(async () => ({ stopped: true })),
  unpairBrowserLink: vi.fn(async () => ({ paired: false })),
  addBrowserTrustedSite: vi.fn(async (host: string) => ({ trusted_sites: [host] })),
  removeBrowserTrustedSite: vi.fn(async () => ({ trusted_sites: [] })),
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

const DOCTOR = {
  has_any_key: true,
  local_model: false,
  can_answer: true,
  configured_providers: ["openrouter"],
  default_model: "openrouter/x",
  tiers: { weak: "a", mid: "b", top: "c" },
  memory_backend: "json",
  cache: false,
  sandbox: "local",
};

function config(over: Record<string, unknown> = {}) {
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
    ...over,
  };
}

const LABEL = "Claude Code agents";

/** Claude Code compatibility for subagents: on by the owner's decision, and the role map is visible. */
describe("Settings — Claude Code agents", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getDoctor).mockResolvedValue(DOCTOR as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "", instructions: "" } as never);
  });

  it("is on for a server that predates the block, and says a file can only narrow", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Experimental" });

    expect(within(card).getByRole("switch", { name: LABEL })).toBeChecked();
    expect(card).toHaveTextContent("can only narrow what the session already has");
  });

  it("saves the switch under its own variable", async () => {
    const user = userEvent.setup();
    vi.mocked(getConfig).mockResolvedValue(config({ claude_agents: { enabled: true } }) as never);
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Experimental" });

    await user.click(within(card).getByRole("switch", { name: LABEL }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_CLAUDE_AGENTS: "false" });
  });

  it("shows the tier each model alias means and saves a change to one of them", async () => {
    const user = userEvent.setup();
    vi.mocked(getConfig).mockResolvedValue(
      config({
        claude_agents: { enabled: true, roles: { opus: "top", sonnet: "mid", haiku: "weak", fable: "top" } },
      }) as never,
    );
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Experimental" });

    const sonnet = within(card).getByRole("combobox", { name: "Claude agents: sonnet means" });
    expect(sonnet).toHaveValue("mid");
    await user.selectOptions(sonnet, "weak");
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_CLAUDE_AGENT_SONNET: "weak" });
  });
});
