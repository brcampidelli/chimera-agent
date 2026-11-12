import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { getClaudeHooks, getConfig, getDoctor, getInstructions, getMessaging, patchConfig, trustClaudeHooks } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getClaudeHooks: vi.fn(),
  trustClaudeHooks: vi.fn(async () => ({ trust: "trusted" })),
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
 * The owner's lifecycle hooks (owner's decision, 2026-10-05; docs/hooks-threat-model.md). Two
 * switches, both off when the server says nothing, both saved as the owner set them. The hint says
 * the part the switch alone cannot: a hook only tightens, and the agent cannot turn this on.
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

describe("Settings — the owner's lifecycle hooks", () => {
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

  it("reads both switches as off when the server says nothing, and says a hook never allows", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);

    const hooks = await screen.findByRole("switch", { name: "Run my lifecycle hooks" });
    expect(hooks).toHaveAttribute("aria-checked", "false");
    const host = screen.getByRole("switch", { name: "Let shell hooks run without a sandbox" });
    expect(host).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText(/never allow/i)).toBeInTheDocument();
  });

  it("saves each switch as the owner sets it", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ hooks: false, hooks_host_exec: false }) as never,
    );
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: "Run my lifecycle hooks" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_HOOKS: "true" });

    await user.click(screen.getByRole("switch", { name: "Let shell hooks run without a sandbox" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledTimes(2));
    expect(vi.mocked(patchConfig).mock.calls[1][0]).toEqual({ CHIMERA_HOOKS_HOST_EXEC: "true" });
  });

  it("reads both Claude Code origins as ON when the server says nothing, saves each, and lists what was loaded", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    vi.mocked(getClaudeHooks).mockResolvedValue({
      workspace: "/p",
      runnable_repo: false,
      runnable_user: false,
      sources: [{ origin: "repo", path: "/p/.claude/settings.json", exists: true, enabled: true, hooks: 1, error: "", skipped: [], trust: "trusted" }],
      hooks: [{ origin: "repo", event: "PreToolUse", matcher: "Bash", command: "check.sh", timeout: 30, on_failure: "block", source: "/p/.claude/settings.json" }],
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const repo = await screen.findByRole("switch", { name: "Run hooks from the project's .claude settings" });
    expect(repo).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: "Run hooks from ~/.claude/settings.json" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(await screen.findByText("check.sh")).toBeInTheDocument();
    expect(screen.getByText("PreToolUse")).toBeInTheDocument();
    expect(screen.getByText(/will not run on this machine/i)).toBeInTheDocument();

    await user.click(repo);
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_CLAUDE_HOOKS_REPO: "false" });
  });

  it("shows the hooks a repository wants confirmed BEFORE the button, and sends the fingerprint it showed", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    vi.mocked(getClaudeHooks).mockResolvedValue({
      workspace: "/p",
      runnable_repo: true,
      runnable_user: true,
      sources: [{ origin: "repo", path: "/p/.claude/settings.json", exists: true, enabled: true, hooks: 0, error: "", skipped: [], trust: "changed" }],
      hooks: [],
      pending: [{ origin: "repo", event: "PreToolUse", matcher: "Bash", command: "bash ./scripts/check.sh", timeout: 30, on_failure: "continue", source: "/p/.claude/settings.json" }],
      fingerprint: "abc123",
    } as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    expect(await screen.findByText("bash ./scripts/check.sh")).toBeInTheDocument();
    expect(screen.getByText(/changed since you confirmed it/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm these hooks" }));
    await waitFor(() => expect(trustClaudeHooks).toHaveBeenCalledOnce());
    expect(vi.mocked(trustClaudeHooks).mock.calls[0][1]).toBe("abc123");
  });
});
