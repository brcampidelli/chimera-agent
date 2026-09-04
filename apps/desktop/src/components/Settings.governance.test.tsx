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
 * The trust kernel's switch, and the sentence that says what turning it on costs.
 *
 * The kernel shipped `off` on every surface, with a Security screen that reported an audit log and
 * no control anywhere to turn on the thing writing it — so the one way to get the product's
 * advertised defence was a file the app never mentions. These tests hold the control, and hold the
 * two things that keep it from being a switch people flip blind: the measured price appears when
 * the mode is not `off`, and the webhook field never shows the URL it is storing.
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

describe("Settings — the trust kernel switch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true,
      configured_providers: ["openrouter"],
      default_model: "openrouter/x",
      tiers: { weak: "a", mid: "b", top: "c" },
      memory_backend: "json",
      cache: false,
      sandbox: "local",
    } as never);
    vi.mocked(getInstructions).mockResolvedValue({
      name: "",
      language: "",
      instructions: "",
    } as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
  });

  it("offers the three modes and saves the one chosen", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const select = await screen.findByRole("combobox", { name: "Trust kernel" });
    await user.selectOptions(select, "enforce");

    // The first ARGUMENT, not the whole call: react-query passes its own mutation context as a
    // second one, so `toHaveBeenCalledWith` compares against something the component never wrote.
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_GOVERNANCE: "enforce" });
  });

  it("says nothing about a price while the kernel is off", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    await screen.findByRole("combobox", { name: "Trust kernel" });
    expect(screen.queryByText(/33 real tool calls/i)).not.toBeInTheDocument();
  });

  it("states what enforcement costs, measured, once it is on", async () => {
    // The reason this control does not open a confirmation dialog: the number is on the screen
    // before the decision rather than behind a click after it.
    vi.mocked(getConfig).mockResolvedValue(config({ governance: "observe" }) as never);
    renderWithProviders(<Settings />);

    expect(await screen.findByText(/33 real tool calls/i)).toBeInTheDocument();
    expect(screen.getByText(/8 refused/i)).toBeInTheDocument();
    expect(screen.getByText(/refused in observe too/i)).toBeInTheDocument();
  });

  it("says which surfaces have to be relaunched", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ governance: "enforce" }) as never);
    renderWithProviders(<Settings />);

    // A control that confirms and does nothing spends the trust of every other control here.
    expect(await screen.findByText(/until the app is relaunched/i)).toBeInTheDocument();
  });

  it("never shows the saved webhook, only that there is one", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ approval_webhook_set: true }) as never);
    renderWithProviders(<Settings />);

    const field = await screen.findByRole("textbox", { name: "Where approvals are asked" });
    expect(field).toHaveValue("");
    expect(field).toHaveAttribute("placeholder", expect.stringMatching(/one is saved/i));
  });

  it("invites a webhook when none is saved", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    const field = await screen.findByRole("textbox", { name: "Where approvals are asked" });
    expect(field).toHaveAttribute("placeholder", expect.stringMatching(/^https:/));
  });

  it("saves a webhook that was typed", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const field = await screen.findByRole("textbox", { name: "Where approvals are asked" });
    await user.type(field, "https://hooks.example/abc");
    // The Save button belonging to THIS row. There are several on the screen and clicking the
    // first one saves whichever field happens to be above.
    const row = field.closest("div")!;
    await user.click(within(row).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_APPROVAL_WEBHOOK: "https://hooks.example/abc",
    });
  });
});
