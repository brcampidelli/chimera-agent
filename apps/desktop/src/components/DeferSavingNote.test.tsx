import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { deferSavingText } from "@/components/DeferSavingNote";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDeferSaving,
  getDoctor,
  getInstructions,
  getMessaging,
  patchConfig,
} from "@/lib/api";
import type { DeferSaving } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDeferSaving: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/** `t` that shows which key was chosen and with what — the wording is the i18n table's business. */
const t = (key: string, params?: Record<string, string | number>) =>
  params ? `${key} ${JSON.stringify(params)}` : key;
const num = (n: number) => String(n);

function measured(over: Partial<DeferSaving> = {}): DeferSaving {
  return {
    builtin: { tools: 22, deferred: 14, declared_chars: 12000, deferred_chars: 4000, saving_pct: 66.7 },
    mcp: null,
    mcp_state: "autoload_off",
    ...over,
  };
}

/**
 * The note under each deferral switch is the token half measured on this install — and only that.
 * A loss is said as one, and an absent MCP figure says why it is absent.
 */
describe("deferSavingText", () => {
  it("reports a saving with the measured characters and a rounded percentage", () => {
    expect(deferSavingText(measured(), "builtin", t, num)).toBe(
      'settings.defer.saving {"from":"12000","to":"4000","pct":"67"}',
    );
  });

  it("reports a loss as a loss, not as a smaller saving", () => {
    const loss = measured({
      mcp: { tools: 1, deferred: 1, declared_chars: 300, deferred_chars: 1200, saving_pct: -300 },
      mcp_state: "measured",
    });
    expect(deferSavingText(loss, "mcp", t, num)).toBe(
      'settings.defer.loss {"from":"300","to":"1200","pct":"300"}',
    );
  });

  it.each([
    ["autoload_off", "settings.defer.autoloadOff"],
    ["not_connected", "settings.defer.notConnected"],
    ["no_servers", "settings.defer.noServers"],
  ] as const)("says why there is no MCP figure when the state is %s", (state, key) => {
    expect(deferSavingText(measured({ mcp_state: state }), "mcp", t, num)).toBe(key);
  });
});

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
    applies: {
      CHIMERA_DEFER_TOOLS: "next_conversation",
      CHIMERA_MCP_DEFER: "next_conversation",
    },
    ...over,
  };
}

const SWITCHES = [
  ["Built-in tools on demand", "CHIMERA_DEFER_TOOLS", "tools"],
  ["MCP tools on demand", "CHIMERA_MCP_DEFER", "mcp"],
] as const;

describe("Settings — the deferral switches", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    vi.mocked(getDeferSaving).mockResolvedValue(measured());
    vi.mocked(getDoctor).mockResolvedValue(DOCTOR as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "", instructions: "" } as never);
  });

  it("shows both off, the bench's inconclusive result, and the saving measured here", async () => {
    // No `defer` block: a server one release behind, where both are off.
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Experimental" });

    for (const [label] of SWITCHES) {
      expect(within(card).getByRole("switch", { name: label })).not.toBeChecked();
    }
    expect(card).toHaveTextContent("15 of 30 tasks completed against 18 of 30");
    expect(card).toHaveTextContent("McNemar p = 0.125, inconclusive");
    expect(card).toHaveTextContent("covered built-in tools only");
    await waitFor(() =>
      expect(card).toHaveTextContent("Measured here: 12,000 → 4,000 schema characters per step, 67% less."),
    );
    expect(card).toHaveTextContent("Nothing to measure: MCP servers are loaded only while MCP autoload is on.");
  });

  it("says it could not measure rather than showing nothing when the route fails", async () => {
    vi.mocked(getDeferSaving).mockRejectedValue(new Error("boom"));
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Experimental" });

    await waitFor(() =>
      expect(within(card).getAllByText("Could not measure the saving on this machine.")).toHaveLength(2),
    );
  });

  it.each(SWITCHES)("saves %s under its own key", async (label, env) => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: label }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ [env]: "true" });
  });

  it.each(SWITCHES)("reads %s as on when the server says so", async (label, _env, field) => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ defer: { tools: false, mcp: false, [field]: true } }) as never,
    );
    renderWithProviders(<Settings />);

    expect(await screen.findByRole("switch", { name: label })).toBeChecked();
    for (const [other] of SWITCHES.filter(([l]) => l !== label)) {
      expect(screen.getByRole("switch", { name: other })).not.toBeChecked();
    }
  });
});
