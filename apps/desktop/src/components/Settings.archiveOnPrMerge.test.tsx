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

/**
 * The archive-on-merge switch (`CHIMERA_ARCHIVE_ON_PR_MERGE`), on the autonomy card beside the close loop.
 * ON by default, the owner's decision: a conversation whose PR is merged or closed leaves the list.
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
    pull_requests: false,
    close_loop: false,
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

describe("Settings — archive a conversation when its pull request settles", () => {
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

  it("is ON when the server says nothing about it (the default), and says turning it off keeps conversations listed", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ archive_on_pr_merge: undefined }) as never);
    renderWithProviders(<Settings />);

    const toggle = await screen.findByRole("switch", {
      name: "Archive a conversation when its pull request is merged or closed",
    });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/On by default: switch it off/i)).toBeInTheDocument();
  });

  it("turning it off saves CHIMERA_ARCHIVE_ON_PR_MERGE=false and nothing else", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ archive_on_pr_merge: true }) as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(
      await screen.findByRole("switch", {
        name: "Archive a conversation when its pull request is merged or closed",
      }),
    );

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_ARCHIVE_ON_PR_MERGE: "false" });
  });
});
