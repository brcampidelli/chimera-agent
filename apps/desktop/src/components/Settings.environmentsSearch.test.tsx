import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging } from "@/lib/api";
import { DICTS } from "@/lib/i18n";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  listCodeEnvironments: vi.fn(async () => ({ environments: [], defaults: {}, kinds: [] })),
  listSshHosts: vi.fn(async () => ({ allowed: [], available: [] })),
  listWslDistros: vi.fn(async () => ({ distros: [] })),
  saveCodeEnvironment: vi.fn(),
  removeCodeEnvironment: vi.fn(),
  allowSshHost: vi.fn(),
  forgetSshHost: vi.fn(),
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

function config() {
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
      tiers: { weak: "a", mid: "b", top: "c" },
    },
    memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
    cache: { completion: false, prompt: false },
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [], governance: "off", approval_webhook_set: false, pull_requests: false, close_loop: false, claude_compat: true, worktree_cleanup: true, hooks: false, hooks_host_exec: false, claude_hooks_repo: true, claude_hooks_user: true, claude_user_hooks_host_exec: false },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: [],
  };
}

// The Environments card (WSL, SSH) and its allowed-hosts list were on the General tab but missing
// from the search index, so "SSH", "WSL" or "ambiente" found nothing in the 0.88.0 app.
describe("Settings search - environments", () => {
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
  });

  afterEach(() => {
    localStorage.removeItem("chimera.lang");
  });

  it.each(["WSL", "SSH", "Environments"])("finds the Environments card by %s", async (query) => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.type(await screen.findByRole("textbox", { name: "Search settings" }), query);
    const label = DICTS.en["env.settings.title"];
    expect(await screen.findByRole("button", { name: (name) => name.includes(label) })).toBeTruthy();
  }, 20_000);

  it("finds the allowed SSH hosts by 'SSH'", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "SSH");
    const label = DICTS.en["env.ssh.hosts.title"];
    expect(await screen.findByRole("button", { name: (name) => name.includes(label) })).toBeTruthy();
  }, 20_000);

  it("finds the card by 'ambiente' in Portuguese", async () => {
    localStorage.setItem("chimera.lang", "pt");
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.type(await screen.findByRole("textbox", { name: DICTS.pt["settings.search.label"] }), "ambiente");
    const label = DICTS.pt["env.settings.title"];
    expect(await screen.findByRole("button", { name: (name) => name.includes(label) })).toBeTruthy();
  }, 20_000);
});
