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
 * The strict spend cap (owner's decision, 2026-10-05). Off as shipped, named "strict" on the row so
 * the refusal it can cause is never a surprise, and the hint says both what off allows (one call
 * past a typed ceiling) and that a run with no ceiling is untouched, since limits are warnings.
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

function config(spend?: Record<string, unknown>) {
  return spend ? { ...CONFIG, spend } : CONFIG;
}

const SWITCH = { name: "Strict spend cap" };

describe("Settings — the strict spend cap", () => {
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

  it("reads as off when the server says nothing about it, and says what off and on mean", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    const toggle = await screen.findByRole("switch", SWITCH);
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/passed by at most one call's cost/i)).toBeInTheDocument();
    expect(screen.getByText(/Runs without a ceiling are not affected/i)).toBeInTheDocument();
  });

  it("shows the saved state and saves the owner's choice", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ daily_usd_cap: null, strict_cap: true }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const toggle = await screen.findByRole("switch", SWITCH);
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_STRICT_SPEND_CAP: "false" });
  });
});
