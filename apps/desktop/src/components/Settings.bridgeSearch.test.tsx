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
      tiers: { weak: "a", mid: "b", top: "c" },
    },
    memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
    cache: { completion: false, prompt: false },
    autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: [],
    applies: {},
    ...over,
  };
}

/**
 * The search box finds the Claude bridge, which sits in the Advanced block. Before, the block stayed
 * closed and the index named only its card title, so "ponte" and "operar" found nothing, and a result
 * that switched tabs left the card out of sight.
 */
describe("Settings search — the Claude bridge", () => {
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

    it("finds the bridge switch by 'ponte' and by 'Claude'", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      const search = await screen.findByRole("textbox", { name: DICTS.pt["settings.search.label"] });

      for (const query of ["ponte", "Claude"]) {
        await user.clear(search);
        await user.type(search, query);
        expect(
          await screen.findByRole("button", { name: /Permitir que o Claude opere este app/ }),
        ).toBeInTheDocument();
      }
    });

    it("finds 'Controle total' by 'Claude'", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      await user.type(await screen.findByRole("textbox", { name: DICTS.pt["settings.search.label"] }), "Claude");
      expect(await screen.findByRole("button", { name: /Controle total/ })).toBeInTheDocument();
    });

    it("opens the Advanced block, scrolls to and focuses the switch", async () => {
      const user = userEvent.setup();
      const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
      renderWithProviders(<Settings />);
      await user.type(await screen.findByRole("textbox", { name: DICTS.pt["settings.search.label"] }), "ponte");
      await user.click(await screen.findByRole("button", { name: /Permitir que o Claude opere este app/ }));

      const bridge = screen.getByRole("switch", { name: "Permitir que o Claude opere este app" });
      expect(bridge).toHaveFocus();
      expect(bridge.closest("details")).toHaveAttribute("open");
      expect(scroll.mock.instances).toContain(bridge);
      scroll.mockRestore();
    });

    it("with the bridge off, 'Controle total' lands on its card, since its switch is disabled", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      await user.type(await screen.findByRole("textbox", { name: DICTS.pt["settings.search.label"] }), "Controle total");
      await user.click(await screen.findByRole("button", { name: /Controle total/ }));

      expect(screen.getByRole("heading", { name: "Claude" })).toHaveFocus();
    });
  });

  describe("in English", () => {
    it("finds the bridge switch by 'bridge'", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "bridge");
      expect(
        await screen.findByRole("button", { name: /Allow Claude to operate this app/ }),
      ).toBeInTheDocument();
    });

    it("reaches an Advanced card the same way, by its title", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "Diagnostics");
      await user.click(await screen.findByRole("button", { name: /Diagnostics/ }));

      expect(screen.getByRole("heading", { name: "Diagnostics" })).toHaveFocus();
      expect(screen.getByRole("heading", { name: "Diagnostics" }).closest("details")).toHaveAttribute("open");
    });
  });

  describe("the Advanced block's open state", () => {
    it("starts closed, and closes again when the person leaves General and comes back", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      const summary = (await screen.findByText("Advanced")).closest("summary")!;
      await user.click(summary);
      expect(summary.closest("details")).toHaveAttribute("open");

      await user.click(screen.getByRole("tab", { name: /Appearance/ }));
      await user.click(screen.getByRole("tab", { name: /General/ }));
      expect(screen.getByText("Advanced").closest("details")).not.toHaveAttribute("open");
    });

    it("a search result opens the block it points into", async () => {
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      // The block starts closed (first test); while a search is showing, the tab panel is not rendered.
      await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "bridge");
      await user.click(await screen.findByRole("button", { name: /Allow Claude to operate this app/ }));
      const bridge = screen.getByRole("switch", { name: "Allow Claude to operate this app" });
      expect(bridge.closest("details")).toHaveAttribute("open");
    });
  });

  // One word from each language's search terms, chosen to be absent from that language's label, so
  // the switch can only be found through the terms key, not through the label.
  describe.each(Object.keys(DICTS) as Lang[])("the bridge switch in %s", (lang) => {
    it("is found by a search term that the label does not contain", async () => {
      const label = DICTS[lang]["settings.row.desktopBridge"];
      const term = DICTS[lang]["settings.search.terms.bridge"]
        .split(",")
        .map((word) => word.trim())
        .find((word) => word && !label.toLocaleLowerCase().includes(word.toLocaleLowerCase()));
      expect(term, `${lang}: a term outside the label`).toBeTruthy();

      localStorage.setItem("chimera.lang", lang);
      const user = userEvent.setup();
      renderWithProviders(<Settings />);
      await user.type(await screen.findByRole("textbox", { name: DICTS[lang]["settings.search.label"] }), term!);
      expect(
        await screen.findByRole("button", { name: (name) => name.includes(label) }),
      ).toBeInTheDocument();
    });
  });

  it("carries the search words and the bridge link in every interface language", () => {
    for (const lang of Object.keys(DICTS) as Lang[]) {
      expect(DICTS[lang]["settings.search.terms.bridge"], lang).toBeTruthy();
      expect(DICTS[lang]["governance.access.bridgeTurnOn"], lang).toBeTruthy();
    }
  });
});
