import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging, listEndpoints } from "@/lib/api";
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
  listEndpoints: vi.fn(async () => ({ endpoints: [] })),
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
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [], pull_requests: false, close_loop: false, archive_on_pr_merge: true },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: [],
    applies: {},
  };
}

/**
 * The named-endpoints card sits on Connections, not General, so the search has to change tab as well
 * as reveal the card. Each language is checked by a term its label and hint do not contain: the card
 * can only be found through the terms key, the one the search index names.
 */
describe("Settings search — OpenAI-compatible endpoints", () => {
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
    vi.mocked(listEndpoints).mockResolvedValue({ endpoints: [] });
  });

  afterEach(() => {
    localStorage.removeItem("chimera.lang");
  });

  describe.each(["en", "pt"] as Lang[])("in %s", (lang) => {
    beforeEach(() => localStorage.setItem("chimera.lang", lang));

    it("finds the card by a server name, opens Connections and shows the card", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      const search = await screen.findByRole("textbox", { name: DICTS[lang]["settings.search.label"] });
      const label = DICTS[lang]["settings.card.endpoints"];

      await user.type(search, "LM Studio");
      await user.click(await screen.findByRole("button", { name: (name) => name.includes(label) }));

      expect(await screen.findByRole("heading", { name: label })).toBeInTheDocument();
      expect(document.getElementById("settings-card-endpoints")).not.toBeNull();
    }, 20_000);
  });
});
