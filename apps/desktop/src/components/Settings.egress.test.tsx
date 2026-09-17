import { screen, waitFor, within } from "@testing-library/react";
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
  getOllamaModels: vi.fn(async () => ({
    base_url: "",
    reachable: false,
    models: [],
    reason: "no_url",
  })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/**
 * The destinations where a query-string GET is not treated as a way out.
 *
 * The rule this narrows asks about every `http_get` with a `?query` once a run has read anything
 * external, which is five questions per session of external-read work by `bench/injection`'s own
 * count. Without a control here the only way to answer "too many" was `CHIMERA_APPROVAL=allow` —
 * yes to everything escalated — or a file the app never mentions. So the test that matters is that
 * the row exists, shows what it holds, and writes the narrow key rather than the blunt one.
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
    egress_allow: ["api.github.com"],
  },
  sandbox: { mode: "local", image: "python:3.12-slim" },
  server: { token_set: false },
  mcp: { autoload: false },
  automation: { cron: true },
  guard: { chat: false },
  providers: [],
};

describe("Settings — the destinations you declared", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
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

  it("shows the hosts it is holding, rather than a mask", async () => {
    // Unlike the webhook beside it, this is not a credential: it is a statement the owner made, and
    // a list you cannot read back is a list you cannot correct.
    renderWithProviders(<Settings />);
    const field = await screen.findByRole("textbox", { name: /Fetching without asking/i });
    expect(field).toHaveValue("api.github.com");
  });

  it("writes the narrow key, not the blunt one", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const field = await screen.findByRole("textbox", { name: /Fetching without asking/i });

    await user.clear(field);
    await user.type(field, "api.github.com, raw.githubusercontent.com");
    // Saved by the button beside it, never on blur: every other text row on this screen commits the
    // same way, and a field that wrote as you tabbed away would save half-typed hosts.
    // Scoped to this row: every text row on the screen has a Save of its own.
    const row = field.parentElement as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalled());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_EGRESS_ALLOW: "api.github.com, raw.githubusercontent.com",
    });
  });
});
