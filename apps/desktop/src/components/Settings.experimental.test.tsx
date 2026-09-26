import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  patchConfig,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  // Settings shows the inline-suggestion acceptance rate now.
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  // Answers, rather than being left undefined: the Ollama picker asks on mount, and an unresolved
  // query would put every test here through a rejected promise for a control none of them is about.
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

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
    memory: {
      backend: "json",
      semantic: false,
      auto_consolidate: false,
      remember_from_chat: false,
    },
    cache: { completion: false, prompt: false },
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: [],
    // Mirrors `config_api.APPLIES_WHEN` for the one of the three that waits.
    applies: { CHIMERA_BROWSER_SITUATION: "next_conversation" },
    ...over,
  };
}

const SWITCHES = [
  ["Hand over at browser walls", "CHIMERA_BROWSER_SITUATION", "browser_situation"],
  ["Web research sub-agent", "CHIMERA_RESEARCH_AGENT", "research_agent"],
  ["Explorer contract", "CHIMERA_EXPLORER_CONTRACT", "explorer_contract"],
] as const;

/**
 * Three study-25 modules were wired, read by the product, and reachable only through `.env`. Their
 * measurements did not recommend them, so they ship off — and the card says what was measured, so
 * the person switching one on knows what the numbers were before they do.
 */
describe("Settings — the Experimental card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(config() as never);
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
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue({
      name: "",
      language: "",
      instructions: "",
    } as never);
  });

  it("groups the three switches under Experimental, all off, each saying what was measured", async () => {
    // No `experimental` block at all: a server one release behind. Off is the shipped behaviour.
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Experimental" });

    for (const [label] of SWITCHES) {
      expect(within(card).getByRole("switch", { name: label })).not.toBeChecked();
    }
    expect(card).toHaveTextContent("caught 9 of 15 walls on fresh pages");
    expect(card).toHaveTextContent("at 4.9× the tokens");
    expect(card).toHaveTextContent("Not measured yet");
    // Only the browser module waits for a new conversation; the other two apply on the next turn.
    expect(within(card).getAllByText(/next conversation/i)).toHaveLength(1);
  });

  it.each(SWITCHES)("saves %s under its own key", async (label, env) => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: label }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ [env]: "true" });
  });

  it.each(SWITCHES)("reads %s as on when the server says so, and switches it off", async (label, env, field) => {
    vi.mocked(getConfig).mockResolvedValue(
      config({
        experimental: {
          browser_situation: false,
          research_agent: false,
          explorer_contract: false,
          [field]: true,
        },
      }) as never,
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const on = await screen.findByRole("switch", { name: label });
    expect(on).toBeChecked();
    for (const [other] of SWITCHES.filter(([l]) => l !== label)) {
      expect(screen.getByRole("switch", { name: other })).not.toBeChecked();
    }
    await user.click(on);
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ [env]: "false" });
  });
});
