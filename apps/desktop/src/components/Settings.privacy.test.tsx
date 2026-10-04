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
 * What an OpenRouter route may keep, from the app (study 29, P5.6).
 *
 * Both switches narrow which upstream providers may answer, so they ship off and nothing has
 * measured that they should be on. The owner decided every switch must be changeable from the app,
 * so each has a row, writes its own key, and a server one release behind reads as the shipped
 * default rather than as a blank control.
 */
function config(privacy?: Record<string, unknown>) {
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
    providers: [],
    ...(privacy ? { privacy } : {}),
  };
}

const DATA = /OpenRouter: data collection/i;
const ZDR = /OpenRouter: zero data retention only/i;

describe("Settings — what an OpenRouter route may keep", () => {
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
    vi.mocked(getInstructions).mockResolvedValue({ text: "", path: "" } as never);
    vi.mocked(getMessaging).mockResolvedValue({ running: false, channels: [] } as never);
  });

  it("reads a server without the block as the shipped default: allow, and retention not restricted", async () => {
    renderWithProviders(<Settings />);
    expect(await screen.findByRole("combobox", { name: DATA })).toHaveValue("allow");
    expect(screen.getByRole("switch", { name: ZDR })).not.toBeChecked();
  });

  it("writes deny under its own key", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.selectOptions(await screen.findByRole("combobox", { name: DATA }), "deny");
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_OPENROUTER_DATA_COLLECTION: "deny",
    });
  });

  it("writes zero data retention under its own key", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.click(await screen.findByRole("switch", { name: ZDR }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_OPENROUTER_ZDR: "true" });
  });

  it("shows what the server holds, and turns it back off", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ openrouter_data_collection: "deny", openrouter_zdr: true, routes: [], telemetry: false }) as never,
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    expect(await screen.findByRole("combobox", { name: DATA })).toHaveValue("deny");
    const zdr = screen.getByRole("switch", { name: ZDR });
    expect(zdr).toBeChecked();
    await user.click(zdr);
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_OPENROUTER_ZDR: "false" });
  });
});
