import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  getShellPrefs,
  getWeeklyReview,
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
  getShellPrefs: vi.fn(),
  getWeeklyReview: vi.fn(),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  patchShellPrefs: vi.fn(),
  putInstructions: vi.fn(),
  putWeeklyReview: vi.fn(),
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
      voice_model: "",
      voice_work_model: "",
      tiers: { weak: "a", mid: "b", top: "c" },
    },
    memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
    cache: { completion: false, prompt: false },
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true, notify_failures: true },
    conversations: { archive_after_days: 21 },
    guard: { chat: false },
    providers: [],
    applies: {},
    ...over,
  };
}

/**
 * The owner's rule for study 29: every on/off switch it shipped can be changed from this screen,
 * not only from `.env`, a JSON file or the tray menu. These are the rows that were missing, mounted
 * in the real screen — and the one switch that is deliberately absent.
 */
describe("Settings — every switch of the batch has a row", () => {
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
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "", instructions: "" } as never);
    vi.mocked(getShellPrefs).mockResolvedValue({
      available: true,
      keep_in_tray: false,
      call_attention: true,
      quick_entry: false,
      quick_entry_chord: "CommandOrControl+Shift+Space",
      start_at_sign_in: false,
      sign_in_requested: null,
      problem: "",
      unreadable: false,
    });
    vi.mocked(getWeeklyReview).mockResolvedValue({ proposed: false, job_id: "", enabled: false, posts_to: "" });
  });

  it("switches the scheduled-job failure notice off through its own setting", async () => {
    renderWithProviders(<Settings />);

    const control = await screen.findByRole("switch", { name: "Say when a job could not run" });
    expect(control).toHaveAttribute("aria-checked", "true");
    await userEvent.click(control);

    // The screen's mutation hands react-query's context as a second argument; the first is the patch.
    expect(vi.mocked(patchConfig).mock.calls.map((call) => call[0])).toEqual([
      { CHIMERA_CRON_NOTIFY_FAILURES: "false" },
    ]);
  });

  it("reads the notice as on against a server that does not report it", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ automation: { cron: true } }) as never);
    renderWithProviders(<Settings />);

    expect(await screen.findByRole("switch", { name: "Say when a job could not run" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("mounts the window-and-tray card, the archive row and the weekly review", async () => {
    renderWithProviders(<Settings />);

    expect(await screen.findByRole("heading", { name: "Window and tray" })).toBeInTheDocument();
    expect(screen.getByLabelText("Archive idle conversations after")).toHaveValue("21");
    expect(await screen.findByRole("switch", { name: "Weekly review" })).toBeInTheDocument();
  });

  it("has no row that approves from a chat bot", async () => {
    // Deliberately `.env`-only: approving from a chat channel widens who can approve.
    renderWithProviders(<Settings />);
    await screen.findByRole("heading", { name: "Window and tray" });

    for (const control of screen.getAllByRole("switch")) {
      expect(control.getAttribute("aria-label") ?? "").not.toMatch(/approv\w* (from|via|by) (the )?chat/i);
    }
    expect(screen.queryByText(/CHIMERA_APPROVE_VIA_CHAT/)).not.toBeInTheDocument();
  });
});
