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
  getBrowserLink: vi.fn(async () => ({ paired: false, connected: false })),
  getBrowserTrustedSites: vi.fn(async () => ({ trusted_sites: [] })),
  stopBrowserLink: vi.fn(async () => ({ stopped: true })),
  unpairBrowserLink: vi.fn(async () => ({ paired: false })),
  addBrowserTrustedSite: vi.fn(async (host: string) => ({ trusted_sites: [host] })),
  removeBrowserTrustedSite: vi.fn(async () => ({ trusted_sites: [] })),
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

const LABEL = "Let that loop merge a green pull request";

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
    pull_requests: true,
    close_loop: true,
    close_loop_merge: false,
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

/**
 * The merge is its own switch, off, and separate from the close loop. It was one switch until the owner
 * decided on 2026-11-10 that opening a pull request should be on by default while merging stays off.
 */
describe("Settings — the close loop's merge switch", () => {
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

  it("reads as off when the server says it is off, and names what it never merges", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ close_loop_merge: false }) as never);
    renderWithProviders(<Settings />);

    const toggle = await screen.findByRole("switch", { name: LABEL });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/billing, a destructive migration, row-level security or a grant/i)).toBeInTheDocument();
  });

  it("saves the owner's choice under CHIMERA_CLOSE_LOOP_MERGE and never under the loop's own switch", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ close_loop_merge: false }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: LABEL }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_CLOSE_LOOP_MERGE: "true" });
  });

  it("reads as off when the server reports nothing about it, which is the schema's own default", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ close_loop_merge: undefined }) as never);
    renderWithProviders(<Settings />);

    expect(await screen.findByRole("switch", { name: LABEL })).toHaveAttribute("aria-checked", "false");
  });
});
