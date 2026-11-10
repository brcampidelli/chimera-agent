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
 * The search box finds the two agent-authority switches on the autonomy card, which sits on General
 * and is not in the Advanced block. Before, neither row was indexed, so "pull request" and "ciclo"
 * found nothing; the card itself was found only by its title, and "autonomia" found nothing at all.
 */
describe("Settings search — the pull-request and close-the-loop switches", () => {
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

  describe("in Portuguese", () => {
    beforeEach(() => localStorage.setItem("chimera.lang", "pt"));

    it("finds the close-the-loop switch by 'pull request' and by 'ciclo', and focuses it", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      const search = await screen.findByRole("textbox", { name: DICTS.pt["settings.search.label"] });
      const label = DICTS.pt["settings.row.closeLoop"];

      for (const query of ["pull request", "ciclo"]) {
        await user.clear(search);
        await user.type(search, query);
        await user.click(await screen.findByRole("button", { name: (name) => name.includes(label) }));
        const switchEl = screen.getByRole("switch", { name: label });
        expect(switchEl, query).toHaveFocus();
      }
    }, 20_000);

    it("finds the pull-request switch by 'PR' and focuses it", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      const label = DICTS.pt["settings.row.pullRequests"];
      await user.type(await screen.findByRole("textbox", { name: DICTS.pt["settings.search.label"] }), "PR");
      await user.click(await screen.findByRole("button", { name: (name) => name.includes(label) }));
      expect(screen.getByRole("switch", { name: label })).toHaveFocus();
    }, 20_000);

    it("finds the autonomy card by 'autonomia' and focuses its heading", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      await user.type(await screen.findByRole("textbox", { name: DICTS.pt["settings.search.label"] }), "autonomia");
      await user.click(await screen.findByRole("button", { name: /Quanto ele pode fazer/ }));
      expect(screen.getByRole("heading", { name: "Quanto ele pode fazer" })).toHaveFocus();
    }, 20_000);
  });

  describe("in English", () => {
    it("finds the close-the-loop switch by 'close the loop' and focuses it", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      const label = DICTS.en["settings.row.closeLoop"];
      await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "close the loop");
      await user.click(await screen.findByRole("button", { name: (name) => name.includes(label) }));
      expect(screen.getByRole("switch", { name: label })).toHaveFocus();
    }, 20_000);
  });

  // One term from each language's close-the-loop vocabulary that the label and hint do not contain, so
  // the switch can only be found through the terms key.
  describe.each(Object.keys(DICTS) as Lang[])("the close-the-loop switch in %s", (lang) => {
    it("is found by a search term that neither its label nor its hint contains", async () => {
      const label = DICTS[lang]["settings.row.closeLoop"];
      const haystack = `${label} ${DICTS[lang]["settings.hint.closeLoopNoSecret"]}`.toLocaleLowerCase();
      const term = DICTS[lang]["settings.search.terms.closeLoop"]
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

  it("carries the search words for the three rows in every interface language", () => {
    for (const lang of Object.keys(DICTS) as Lang[]) {
      expect(DICTS[lang]["settings.search.terms.closeLoop"], lang).toBeTruthy();
      expect(DICTS[lang]["settings.search.terms.pullRequests"], lang).toBeTruthy();
      expect(DICTS[lang]["settings.search.terms.autonomy"], lang).toBeTruthy();
    }
  });
});
