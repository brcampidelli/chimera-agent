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
    applies: {},
    ...over,
  };
}

const OPERATE = "Allow Claude to operate this app";
const FULL = "Full control";

function bridge(enabled: boolean, full: boolean) {
  return config({ bridge: { enabled, full } });
}

/**
 * The desktop bridge's two switches (`chimera/api/desktop_bridge.py`). Both ship off; the second is
 * inert without the first, and says plainly what it allows — Claude approving without the owner,
 * including after reading a prompt-injected page — before anyone turns it on.
 */
describe("Settings — the Claude card", () => {
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

  it("ships both off, the second disabled, each saying what it allows", async () => {
    // No `bridge` block at all: a server one release behind reads as off, never as on.
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Claude" });

    expect(within(card).getByRole("switch", { name: OPERATE })).not.toBeChecked();
    const full = within(card).getByRole("switch", { name: FULL });
    expect(full).not.toBeChecked();
    expect(full).toBeDisabled();
    expect(card).toHaveTextContent("Approvals stay with you; keys are never shared.");
    expect(card).toHaveTextContent("Claude can also approve or deny actions without you");
    expect(card).toHaveTextContent("A prompt-injected page or message the agent reads could lead Claude to approve something.");
    expect(card).toHaveTextContent('Turn on "Allow Claude to operate this app" first.');
  });

  it("saves the first switch under its own key", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: OPERATE }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_DESKTOP_BRIDGE: "true" });
  });

  it("does nothing when the disabled second switch is pressed", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: FULL }));
    expect(patchConfig).not.toHaveBeenCalled();
  });

  it("with the first on, shows how to connect and lets full control be saved", async () => {
    vi.mocked(getConfig).mockResolvedValue(bridge(true, false) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Claude" });

    expect(within(card).getByRole("switch", { name: OPERATE })).toBeChecked();
    expect(card).toHaveTextContent("claude mcp add chimera-desktop -- chimera mcp desktop");
    const full = within(card).getByRole("switch", { name: FULL });
    expect(full).toBeEnabled();
    await user.click(full);
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_DESKTOP_BRIDGE_FULL: "true" });
  });

  it("reads full control back as on, and switches it off", async () => {
    vi.mocked(getConfig).mockResolvedValue(bridge(true, true) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const full = await screen.findByRole("switch", { name: FULL });
    expect(full).toBeChecked();
    await user.click(full);
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_DESKTOP_BRIDGE_FULL: "false" });
  });
});
