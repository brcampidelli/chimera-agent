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
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/**
 * The "Governance and audit" card: study 30's opt-in rules and the provider-gateway wire log, each
 * off as shipped. Before it, four of them could be turned on only by editing `.env`. These hold that
 * a server without the block reads as all off (which is what it does), that each switch writes its
 * own key and only that key, that the wire log says plainly what it records and that it blocks
 * nothing, and that a deadline on a band that is off is said to do nothing.
 */
const BASE = {
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

const ALL_OFF = {
  wire_log: false,
  exfil_host_path: false,
  shell_fetch_guard: false,
  taint_rope_lite: false,
  arm_on_recalled_lessons: false,
  band_deadline_s: null,
  band_on: false,
};

function config(block?: Record<string, unknown>) {
  return block ? { ...BASE, governance_audit: { ...ALL_OFF, ...block } } : BASE;
}

/** Switch label -> the key it writes. */
const SWITCHES: Array<[string, string]> = [
  ["Provider exchange log", "CHIMERA_WIRE_LOG"],
  ["Ask before a secret leaves in a URL", "CHIMERA_EXFIL_HOST_PATH"],
  ["Ask before cloning an unnamed repository", "CHIMERA_SHELL_FETCH_GUARD"],
  ["Recalled unverified lessons arm the run", "CHIMERA_ARM_ON_RECALLED_LESSONS"],
  ["Check where tool arguments came from (ROPE-lite)", "CHIMERA_TAINT_ROPE_LITE"],
];

async function card() {
  return within(await screen.findByRole("region", { name: "Governance and audit" }));
}

describe("Settings — governance and audit", () => {
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

  it("reads every switch as off when the server says nothing about them", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    const c = await card();
    for (const [label] of SWITCHES) {
      expect(c.getByRole("switch", { name: label })).toHaveAttribute("aria-checked", "false");
    }
    expect(c.getByRole("textbox", { name: "Decision deadline (seconds)" })).toHaveValue("");
    expect(c.getByText(/the desktop bridge cannot change any of them/i)).toBeInTheDocument();
  });

  it("says what the wire log records, that it blocks nothing, and where the limits are written", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    const c = await card();
    expect(c.getByText(/digests of what was sent and of what came back/i)).toBeInTheDocument();
    expect(c.getByText(/Never the message text, never a key/i)).toBeInTheDocument();
    expect(c.getByText(/only records and blocks nothing/i)).toBeInTheDocument();
    expect(c.getByText(/docs\/governance-for-deployers\.md/)).toBeInTheDocument();
  });

  it("states each rule's measurement, the failed verdict included", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    const c = await card();
    expect(c.getByText(/10\.7% false questions against a 10% ceiling/)).toBeInTheDocument();
    expect(c.getByText(/nothing measured how often real sessions clone/i)).toBeInTheDocument();
    expect(c.getByText(/its cost was never measured/i)).toBeInTheDocument();
    expect(c.getByText(/the published verdict is FAIL/)).toBeInTheDocument();
  });

  it.each(SWITCHES)("switching %s on writes %s and nothing else", async (label, key) => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click((await card()).getByRole("switch", { name: label }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ [key]: "true" });
  });

  it("shows a saved switch as on, and switching it off writes false", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ wire_log: true }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const toggle = (await card()).getByRole("switch", { name: "Provider exchange log" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_WIRE_LOG: "false" });
  });

  it("says a late decision becomes REVIEW, and that a deadline on a band that is off does nothing", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    const c = await card();
    expect(c.getByText(/becomes REVIEW, a question for you, and is never read as ALLOW/)).toBeInTheDocument();
    expect(c.getByText(/The governance band is off on this install/)).toBeInTheDocument();
  });

  it("drops the band-off line once the band runs, and saves the deadline trimmed", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ band_on: true, band_deadline_s: 5 }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const c = await card();
    expect(c.queryByText(/The governance band is off on this install/)).not.toBeInTheDocument();
    const field = c.getByRole("textbox", { name: "Decision deadline (seconds)" });
    expect(field).toHaveValue("5");
    await user.clear(field);
    await user.type(field, " 2.5 ");
    const row = field.parentElement as HTMLElement;
    await user.click(within(row).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_GOVERNANCE_BAND_DEADLINE_S: "2.5",
    });
  });

  it("clears the deadline by saving it empty", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ band_deadline_s: 5 }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const field = (await card()).getByRole("textbox", { name: "Decision deadline (seconds)" });
    await user.clear(field);
    await user.click(within(field.parentElement as HTMLElement).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_GOVERNANCE_BAND_DEADLINE_S: "",
    });
  });
});
