import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  // Settings shows the inline-suggestion acceptance rate now.
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
    ollama_base_url: "http://localhost:11434",
    complete_model: "",
  },
  memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
  cache: { completion: false, prompt: false },
  autonomy: { reach: "read_only", approval: "", host_exec: "ask", denied_tools: [] },
  sandbox: { mode: "local", image: "python:3.12-slim" },
  browser: { headless: true },
  server: { token_set: false },
  mcp: { autoload: false },
  automation: { cron: true },
  guard: { chat: false },
  providers: [],
  applies: {},
  pinned: [],
};

function config(over: Record<string, unknown> = {}) {
  return { ...CONFIG, ...over };
}

/**
 * A save this screen cannot keep, said before it is made.
 *
 * `.env` loses to a real environment variable, and `PATCH /api/config` writes `.env`. So on a server
 * started with `-e CHIMERA_REACH=read_only`, changing reach here succeeds, holds for the session and
 * reverts at the next launch. The delay is the whole problem: by the time the old value is back
 * nobody connects it to the save, and the screen has spent its credibility on a control that
 * reported a change it could not keep.
 *
 * Reachable at all because the app can be pointed at a server somebody else deployed — the local
 * sidecar inherits nothing, which is why the list is normally empty and the note normally silent.
 */
describe("Settings — what the server's environment pins", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(config() as never);
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

  const note = /Fixed in this server's environment/i;

  it("says so on the row that is pinned", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ pinned: ["CHIMERA_REACH"] }) as never);
    renderWithProviders(<Settings />);

    const card = await screen.findByRole("region", { name: "How much it may do" });
    expect(within(card).getAllByText(note)).toHaveLength(1);
  });

  it("stays silent on the rows that are not", async () => {
    // The reason this is a per-row note and not a banner: the screen has thirty controls, and a
    // warning that does not say WHICH one is a warning the reader has to go and re-derive.
    vi.mocked(getConfig).mockResolvedValue(config({ pinned: ["CHIMERA_REACH"] }) as never);
    renderWithProviders(<Settings />);

    await screen.findByRole("region", { name: "How much it may do" });
    const sandbox = screen.getByRole("region", { name: "Cache & sandbox" });
    expect(within(sandbox).queryByText(note)).not.toBeInTheDocument();
  });

  it("names each pinned setting rather than one of them", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ pinned: ["CHIMERA_REACH", "CHIMERA_SANDBOX", "CHIMERA_SERVER_TOKEN"] }) as never,
    );
    renderWithProviders(<Settings />);

    expect(await screen.findAllByText(note)).toHaveLength(3);
  });

  it("shows nothing at all on an ordinary install", async () => {
    // The normal case. A caveat that is always on screen teaches the same distrust as a missing one.
    renderWithProviders(<Settings />);

    await screen.findByRole("region", { name: "How much it may do" });
    expect(screen.queryByText(note)).not.toBeInTheDocument();
  });

  it("survives a server too old to send the list", async () => {
    // The app talks to remote servers, which may be a release behind — `pinned` absent must read as
    // "nothing is pinned", not as a crash on the one screen that fixes a bad connection.
    const { pinned: _pinned, ...withoutPinned } = CONFIG;
    vi.mocked(getConfig).mockResolvedValue(withoutPinned as never);
    renderWithProviders(<Settings />);

    await screen.findByRole("region", { name: "How much it may do" });
    expect(screen.queryByText(note)).not.toBeInTheDocument();
  });
});
