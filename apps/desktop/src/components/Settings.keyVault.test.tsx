import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  moveVaultKeys,
  patchConfig,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  moveVaultKeys: vi.fn(),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/**
 * Keys in the OS vault, from the Settings screen (study 29, P7.7).
 *
 * The switch ships off; on, the next key saved goes to the vault and `.env` keeps a comment. What the
 * screen must never do is read "on" over a plain-text key without saying so: with no vault on the
 * machine the hint says it before the switch is touched, and a save that fell back names the keys.
 * The rows show WHERE a key lives — never more of it than the four characters they always showed.
 */
const PROVIDER = {
  env: "OPENROUTER_API_KEY",
  label: "OpenRouter",
  name: "openrouter",
  set: true,
  hint: "…1234",
  llm: true,
  model: "",
  keys_url: "",
};

function config(vault?: Record<string, unknown>, inVault = false) {
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
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [], governance: "off" },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: [{ ...PROVIDER, in_vault: inVault }],
    ...(vault ? { vault } : {}),
  };
}

const SWITCH = /Keep keys in the OS vault/i;

describe("Settings — keys in the OS vault", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(
      config({ enabled: false, available: true, keys: [] }) as never,
    );
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

  it("reads a server without the block as the shipped default: off, and no vault claimed", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);
    expect(await screen.findByRole("switch", { name: SWITCH })).not.toBeChecked();
    expect(screen.getByText(/no vault Chimera can use/i)).toBeInTheDocument();
    // Nothing to move from a machine that has no vault.
    expect(screen.queryByRole("button", { name: /Move into the vault/i })).toBeNull();
  });

  it("says the server token stays in .env, where the tray reads it", async () => {
    // The one key the switch never moves: the desktop shell sends it with every tray look and
    // cannot read the vault. Without this line the owner finds a plain-text token the switch
    // seemed to promise away.
    renderWithProviders(<Settings />);
    await screen.findByRole("switch", { name: SWITCH });
    expect(
      screen.getByText(/server token \(CHIMERA_SERVER_TOKEN\) always stays in \.env/i),
    ).toBeInTheDocument();
  });

  it("turns it on under its own key", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.click(await screen.findByRole("switch", { name: SWITCH }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_KEY_VAULT: "true" });
  });

  it("names the keys a save wrote to .env because there was no vault", async () => {
    vi.mocked(patchConfig).mockResolvedValue({
      updated: ["CHIMERA_KEY_VAULT"],
      vault_fallback: ["OPENROUTER_API_KEY"],
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.click(await screen.findByRole("switch", { name: SWITCH }));
    expect(
      await screen.findByText(/so these were saved to \.env: OPENROUTER_API_KEY/i),
    ).toBeInTheDocument();
  });

  it("marks a key the vault holds, and shows no more of it than before", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ enabled: true, available: true, keys: ["OPENROUTER_API_KEY"] }, true) as never,
    );
    renderWithProviders(<Settings />);
    expect(await screen.findByText("in the OS vault")).toBeInTheDocument();
    expect(screen.getByText(/…1234/)).toBeInTheDocument();
  });

  it("does not move keys into the vault while the switch is off", async () => {
    renderWithProviders(<Settings />);
    expect(await screen.findByRole("button", { name: /Move into the vault/i })).toBeDisabled();
    // Nothing in the vault, so there is nothing to bring back either.
    expect(screen.getByRole("button", { name: /Move back to \.env/i })).toBeDisabled();
  });

  it("moves the keys into the vault with the switch on, and reports by name", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ enabled: true, available: true, keys: [] }) as never,
    );
    vi.mocked(moveVaultKeys).mockResolvedValue({
      moved: ["OPENROUTER_API_KEY"],
      failed: ["TAVILY_API_KEY"],
      skipped: [],
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const into = await screen.findByRole("button", { name: /Move into the vault/i });
    expect(into).toBeEnabled();
    await user.click(into);
    await waitFor(() => expect(moveVaultKeys).toHaveBeenCalledWith("vault"));
    expect(await screen.findByText(/Moved: OPENROUTER_API_KEY/)).toBeInTheDocument();
    expect(screen.getByText(/Stayed where they were: TAVILY_API_KEY/)).toBeInTheDocument();
  });

  it("names a key too large for the vault apart from one that failed", async () => {
    // The server knows why this one stayed — "stayed where they were" would send the owner after a
    // locked vault.
    vi.mocked(getConfig).mockResolvedValue(
      config({ enabled: true, available: true, keys: [] }) as never,
    );
    vi.mocked(moveVaultKeys).mockResolvedValue({
      moved: [],
      failed: [],
      skipped: [],
      too_large: ["CHIMERA_OPENROUTER_KEYS"],
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.click(await screen.findByRole("button", { name: /Move into the vault/i }));
    expect(
      await screen.findByText(/Too large for this computer's vault.*CHIMERA_OPENROUTER_KEYS/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Nothing to move/)).toBeNull();
  });

  it("offers the way back with the switch off, when the vault holds keys", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ enabled: false, available: true, keys: ["OPENROUTER_API_KEY"] }, true) as never,
    );
    vi.mocked(moveVaultKeys).mockResolvedValue({
      moved: ["OPENROUTER_API_KEY"],
      failed: [],
      skipped: [],
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const back = await screen.findByRole("button", { name: /Move back to \.env/i });
    expect(back).toBeEnabled();
    await user.click(back);
    await waitFor(() => expect(moveVaultKeys).toHaveBeenCalledWith("file"));
  });
});
