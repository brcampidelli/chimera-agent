import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  addPoolKey,
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  patchConfig,
  removePoolKey,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  // Settings shows the inline-suggestion acceptance rate now.
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  addPoolKey: vi.fn(async () => ({ provider: "openrouter", count: 2 })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  // Answers, rather than being left undefined: the Ollama picker asks on mount, and an unresolved
  // query would put every test here through a rejected promise for a control none of them is about.
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  removePoolKey: vi.fn(async () => ({ provider: "openrouter", count: 0 })),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

function config() {
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
      ollama_base_url: "http://localhost:11434",
      lm_studio_base_url: "http://localhost:1234/v1",
    },
    memory: {
      backend: "json",
      semantic: false,
      auto_consolidate: false,
      remember_from_chat: false,
      skill_cards: false,
      embed_model: "openrouter/openai/text-embedding-3-small",
    },
    cache: { completion: false, prompt: false },
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: [],
    pools: [
      {
        provider: "openrouter",
        env: "CHIMERA_OPENROUTER_KEYS",
        keys: [
          { index: 0, hint: "…1111" },
          { index: 1, hint: "…2222" },
        ],
      },
      { provider: "openai", env: "CHIMERA_OPENAI_KEYS", keys: [] },
    ],
    applies: {},
  };
}

/**
 * The rotation pools worked for a long time with no way to reach them, and the tempting fix — a text
 * field holding the comma-separated list — has a specific failure: the field must show its value to
 * be editable, the value is secret so it shows the mask, and one Save writes `…abcd` over a working
 * rotation. These assert the shape that makes that impossible rather than unlikely.
 */
describe("Settings — key pools", () => {
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

  it("shows the pool as hints, and offers no control whose value is a key", async () => {
    renderWithProviders(<Settings />);
    await screen.findByText("…1111");
    expect(screen.getByText("…2222")).toBeTruthy();

    // The whole design in one assertion: nothing on screen is pre-filled with a secret, so there is
    // no field to accidentally re-submit.
    for (const box of screen.queryAllByRole("textbox")) {
      expect((box as HTMLInputElement).value).not.toMatch(/…/);
    }
  });

  it("adds one key, and sends only the key that was just typed", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const row = (await screen.findByText("…1111")).closest("div.px-4") as HTMLElement;

    await user.type(within(row).getAllByPlaceholderText(/Paste/i)[0], "sk-or-new3333");
    await user.click(within(row).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addPoolKey).toHaveBeenCalledWith("openrouter", "sk-or-new3333"));
    // Never through the string-writing endpoint: that one takes the WHOLE value, which is exactly
    // the shape that loses the keys this client cannot see.
    expect(patchConfig).not.toHaveBeenCalled();
  });

  it("removes by position, because the client has never held the value", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await screen.findByText("…2222");

    await user.click(screen.getByRole("button", { name: /…2222/ }));

    await waitFor(() => expect(removePoolKey).toHaveBeenCalledWith("openrouter", 1));
  });

  it("says a provider has no pool instead of leaving the row blank", async () => {
    renderWithProviders(<Settings />);
    await screen.findByText("…1111");
    expect(screen.getByText(/No pool/i)).toBeTruthy();
  });

  it("surfaces a refusal rather than reporting a key that was not stored", async () => {
    // The server rejects anything shaped like the mask it hands out. If the screen swallowed that,
    // the user would believe a key is in rotation when it is not.
    vi.mocked(addPoolKey).mockRejectedValueOnce(new Error("HTTP 400"));
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const row = (await screen.findByText("…1111")).closest("div.px-4") as HTMLElement;

    await user.type(within(row).getAllByPlaceholderText(/Paste/i)[0], "…1111");
    await user.click(within(row).getByRole("button", { name: "Add" }));

    expect(await screen.findByText(/Refused/i)).toBeTruthy();
  });

  it("shows the server's reason under the refusal, not only the usual guess", async () => {
    // With the vault on, a pool can outgrow what Windows' Credential Manager keeps per entry; the
    // generic "check it is a key" line alone would be the wrong advice.
    vi.mocked(addPoolKey).mockRejectedValueOnce(
      new Error("CHIMERA_OPENROUTER_KEYS is too large for the Windows Credential Manager"),
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const row = (await screen.findByText("…1111")).closest("div.px-4") as HTMLElement;

    await user.type(within(row).getAllByPlaceholderText(/Paste/i)[0], "sk-or-new3333");
    await user.click(within(row).getByRole("button", { name: "Add" }));

    expect(await screen.findByText(/too large for the Windows Credential Manager/)).toBeTruthy();
  });

  it("says when a pool went to .env in plain text although the vault switch is on", async () => {
    // No vault on this machine: the server writes the whole pool to the file and names it in
    // `vault_fallback`. The switch's row only reads a PATCH's answer, so this card has to say it.
    vi.mocked(addPoolKey).mockResolvedValueOnce({
      provider: "openrouter",
      count: 3,
      vault_fallback: ["CHIMERA_OPENROUTER_KEYS"],
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const row = (await screen.findByText("…1111")).closest("div.px-4") as HTMLElement;

    await user.type(within(row).getAllByPlaceholderText(/Paste/i)[0], "sk-or-new3333");
    await user.click(within(row).getByRole("button", { name: "Add" }));

    expect(await within(row).findByText(/saved to \.env: CHIMERA_OPENROUTER_KEYS/)).toBeTruthy();
  });

  it("says it on a removal too, since the rest of the pool is written back", async () => {
    vi.mocked(removePoolKey).mockResolvedValueOnce({
      provider: "openrouter",
      count: 1,
      vault_fallback: ["CHIMERA_OPENROUTER_KEYS"],
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await screen.findByText("…2222");

    await user.click(screen.getByRole("button", { name: /…2222/ }));

    expect(await screen.findByText(/saved to \.env: CHIMERA_OPENROUTER_KEYS/)).toBeTruthy();
  });

  it("says nothing about the vault when the pool write did not fall back", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const row = (await screen.findByText("…1111")).closest("div.px-4") as HTMLElement;

    await user.type(within(row).getAllByPlaceholderText(/Paste/i)[0], "sk-or-new3333");
    await user.click(within(row).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(addPoolKey).toHaveBeenCalled());
    // Scoped to the pool row and to the note's own shape: the switch row's hint also mentions
    // `.env` ("keys are still saved to .env") on a machine with no vault.
    expect(within(row).queryByText(/saved to \.env: /)).toBeNull();
  });
});
