import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging, putInstructions } from "@/lib/api";
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
  patchConfig: vi.fn(),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

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
  applies: {},
};

/**
 * "Configure my right hand" was the one thing this screen could not do. Three things looked like
 * they already did it — a profile file with no reader in the API, persona memory facts retrieved by
 * keyword so a standing instruction applied only when the wording matched, and an unconditional
 * preamble slot filled by two paths the app never takes.
 */
describe("Settings — who the agent is", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true,
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

  it("sends the whole identity as one record", async () => {
    const user = userEvent.setup();
    vi.mocked(putInstructions).mockResolvedValue({
      name: "Cesar",
      language: "",
      instructions: "be direct",
    } as never);
    renderWithProviders(<Settings />);

    await user.type(await screen.findByLabelText(/Standing instructions/i), "be direct");
    // Scoped: this screen has several Save buttons, and the one that matters is in this card.
    const card = screen.getByRole("region", { name: "Your agent" });
    await user.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(putInstructions).toHaveBeenCalledOnce());
    expect(vi.mocked(putInstructions).mock.calls[0][0]).toEqual({
      name: "",
      language: "",
      instructions: "be direct",
    });
  });

  it("shows what the server stored, not what was typed", async () => {
    // The free text is capped server-side. Echoing the draft would let someone paste more than the
    // budget, watch it come back, and never learn the agent is reading a prefix of it.
    const user = userEvent.setup();
    vi.mocked(putInstructions).mockResolvedValue({
      name: "",
      language: "",
      instructions: "trunc",
    } as never);
    renderWithProviders(<Settings />);

    const box = await screen.findByLabelText(/Standing instructions/i);
    await user.type(box, "truncated-far-past-the-budget");
    // Scoped: this screen has several Save buttons, and the one that matters is in this card.
    const card = screen.getByRole("region", { name: "Your agent" });
    await user.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(box).toHaveValue("trunc"));
  });

  it("offers the interface language in one click, without applying it behind your back", async () => {
    // Two genuinely separate choices: a Brazilian reading English documentation may want exactly
    // that split, so the app knowing the answer is not permission to decide it.
    const user = userEvent.setup();
    vi.mocked(putInstructions).mockResolvedValue({
      name: "",
      language: "English",
      instructions: "",
    } as never);
    renderWithProviders(<Settings />);

    const field = await screen.findByLabelText(/^Answer in$/i);
    expect(field).toHaveValue(""); // nothing was chosen for the user
    await user.click(screen.getByRole("button", { name: /Use interface language/i }));
    expect(field).toHaveValue("English");
  });

  it("says the instructions cannot grant capability, where they are written", async () => {
    // Not in a tooltip. Someone writing "you may run any command" here and then watching the agent
    // refuse deserves to have been told in the same breath.
    renderWithProviders(<Settings />);
    await screen.findByText(/cannot grant capability/i);
  });
});
