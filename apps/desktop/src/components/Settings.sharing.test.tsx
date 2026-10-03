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

function config(over: Record<string, unknown> = {}) {
  return {
    models: {
      default: "openrouter/x", weak: "", mid: "", orchestrator: "", cost_mode: "auto",
      cascade: false, api_base: null, fallback_models: [], tiers: { weak: "a", mid: "b", top: "c" },
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
    ...over,
  };
}

const ALLOW = "Allow sharing conversations";
const EXPIRY = "Links expire after (hours)";

/**
 * The two settings that narrow sharing (`CHIMERA_SHARING`, `CHIMERA_SHARE_EXPIRY_HOURS`). Both rows
 * exist because the owner decided every switch must be changeable from the app; on and empty are
 * what sharing did before they existed, and a server without the block reads as exactly that.
 */
describe("Settings — the Sharing card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true, local_model: false, can_answer: true, configured_providers: ["openrouter"],
      default_model: "openrouter/x", tiers: { weak: "a", mid: "b", top: "c" },
      memory_backend: "json", cache: false, sandbox: "local",
    } as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "", instructions: "" } as never);
  });

  it("reads a server without the block as on and never expiring", async () => {
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Sharing" });
    expect(within(card).getByRole("switch", { name: ALLOW })).toBeChecked();
    const field = within(card).getByRole("textbox", { name: EXPIRY });
    expect(field).toHaveValue("");
    expect(field).toHaveAttribute("placeholder", "never");
    expect(card).toHaveTextContent("stops every existing link from opening");
  });

  it("saves sharing off under its own key", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Sharing" });
    await user.click(within(card).getByRole("switch", { name: ALLOW }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_SHARING: "false" });
  });

  it("shows the saved expiry and saves a new one in hours", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ sharing: { enabled: true, expiry_hours: 24 } }) as never,
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Sharing" });
    const field = within(card).getByRole("textbox", { name: EXPIRY });
    expect(field).toHaveValue("24");
    await user.clear(field);
    await user.type(field, " 72 ");
    await user.click(within(card).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_SHARE_EXPIRY_HOURS: "72" });
  });
});
