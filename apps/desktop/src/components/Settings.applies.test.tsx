import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  patchConfig: vi.fn(),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/**
 * Seven controls on this screen used to save, re-read, show the new value — and change nothing until
 * the app was relaunched. Only one of them said so.
 *
 * Most of that is fixed at the source now (the gateway and the request handlers read through instead
 * of holding a boot-time snapshot). What remains genuinely deferred is deferred for a reason a
 * re-read cannot undo: a cron daemon and a set of MCP subprocesses are already running. Those say so
 * — and the ones that apply immediately stay silent, because a caveat about a delay that does not
 * exist teaches the same distrust as a missing one.
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
  applies: {
    CHIMERA_APP_CRON: "next_launch",
    CHIMERA_MCP_AUTOLOAD: "next_launch",
    CHIMERA_CASCADE: "next_conversation",
    CHIMERA_GUARD_CHAT: "next_conversation",
    CHIMERA_CHAT_MEMORY: "next_conversation",
  },
};

describe("Settings — when a saved change starts applying", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true,
      configured_providers: ["openrouter"],
      default_model: "openrouter/x",
      tiers: { weak: "a", mid: "b", top: "c" },
      memory_backend: "json",
      cache: false,
      sandbox: "local",
    } as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue(
      { name: "", language: "", instructions: "" } as never,
    );
  });

  it("says so on the two that a relaunch is genuinely required for", async () => {
    renderWithProviders(<Settings />);
    const notes = await screen.findAllByText(/applies the next time you start the app/i);
    expect(notes).toHaveLength(2); // scheduled jobs + MCP autoload
  });

  it("says so on the ones that take effect on the next conversation", async () => {
    renderWithProviders(<Settings />);
    const notes = await screen.findAllByText(/applies to your next conversation/i);
    expect(notes).toHaveLength(3); // cascade + guard the chat + remember from chat
  });

  it("stays silent on everything that applies to the next call", async () => {
    // The screen has ~17 controls; only five carry a caveat. If this ever counts more, something
    // grew a delay without anyone declaring it — or a label outlived its reason.
    renderWithProviders(<Settings />);
    await screen.findAllByText(/applies the next time you start the app/i);
    expect(screen.queryAllByText(/^saved —/i)).toHaveLength(5);
  });

  it("shows nothing when the server declares no exceptions at all", async () => {
    // The honest end state: every setting applying to the next call means no caveats anywhere.
    vi.mocked(getConfig).mockResolvedValue({ ...CONFIG, applies: {} } as never);
    renderWithProviders(<Settings />);
    await screen.findByText("a · b · c"); // the screen has rendered
    expect(screen.queryAllByText(/^saved —/i)).toHaveLength(0);
  });
});
