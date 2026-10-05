import { screen, waitFor } from "@testing-library/react";
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
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/**
 * The owner's lifecycle hooks (owner's decision, 2026-10-05; docs/hooks-threat-model.md). Two
 * switches, both off when the server says nothing, both saved as the owner set them. The hint says
 * the part the switch alone cannot: a hook only tightens, and the agent cannot turn this on.
 */
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
  autonomy: {
    reach: "",
    approval: "",
    host_exec: "ask",
    denied_tools: [],
    governance: "off",
    approval_webhook_set: false,
  },
  sandbox: { mode: "local", image: "python:3.12-slim" },
  server: { token_set: false },
  mcp: { autoload: false },
  automation: { cron: true },
  guard: { chat: false },
  providers: [],
};

function config(autonomy: Record<string, unknown> = {}) {
  return { ...CONFIG, autonomy: { ...CONFIG.autonomy, ...autonomy } };
}

describe("Settings — the owner's lifecycle hooks", () => {
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
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "", instructions: "" } as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
  });

  it("reads both switches as off when the server says nothing, and says a hook never allows", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    const hooks = await screen.findByRole("switch", { name: "Run my lifecycle hooks" });
    expect(hooks).toHaveAttribute("aria-checked", "false");
    const host = screen.getByRole("switch", { name: "Let shell hooks run without a sandbox" });
    expect(host).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/never allow/i)).toBeInTheDocument();
  });

  it("saves each switch as the owner sets it", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ hooks: false, hooks_host_exec: false }) as never,
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: "Run my lifecycle hooks" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_HOOKS: "true" });

    await user.click(screen.getByRole("switch", { name: "Let shell hooks run without a sandbox" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledTimes(2));
    expect(vi.mocked(patchConfig).mock.calls[1][0]).toEqual({ CHIMERA_HOOKS_HOST_EXEC: "true" });
  });
});
