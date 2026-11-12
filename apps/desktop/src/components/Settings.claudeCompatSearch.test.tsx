import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging } from "@/lib/api";
import { DICTS, type Lang } from "@/lib/i18n";
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
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [], governance: "off", approval_webhook_set: false, pull_requests: false, close_loop: false, claude_compat: true },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: [],
  };
}

describe("Settings search — the Claude instruction-file switch", () => {
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

  it("finds the switch by 'CLAUDE.md' in English and focuses it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const label = DICTS.en["settings.row.claudeCompat"];
    await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "CLAUDE.md");
    await user.click(await screen.findByRole("button", { name: (name) => name.includes(label) }));
    expect(screen.getByRole("switch", { name: label })).toHaveFocus();
  }, 20_000);

  // One term per language that the label and hint do not contain, so the switch can only be found
  // through its terms key.
  describe.each(Object.keys(DICTS) as Lang[])("the Claude switch in %s", (lang) => {
    it("is found by a search term that neither its label nor its hint contains", async () => {
      const label = DICTS[lang]["settings.row.claudeCompat"];
      const haystack = `${label} ${DICTS[lang]["settings.hint.claudeCompat"]}`.toLocaleLowerCase();
      const term = DICTS[lang]["settings.search.terms.claudeCompat"]
        .split(",")
        .map((word) => word.trim())
        .find((word) => word && !haystack.includes(word.toLocaleLowerCase()));
      expect(term, `${lang}: a term outside the label and hint`).toBeTruthy();

      localStorage.setItem("chimera.lang", lang);
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      await user.type(await screen.findByRole("textbox", { name: DICTS[lang]["settings.search.label"] }), term!);
      await user.click(await screen.findByRole("button", { name: (name) => name.includes(label) }));
      expect(screen.getByRole("switch", { name: label })).toHaveFocus();
    }, 20_000);
  });
});
