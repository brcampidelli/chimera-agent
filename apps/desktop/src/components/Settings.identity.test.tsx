import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging, putInstructions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  // Settings shows the inline-suggestion acceptance rate now.
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
  // Answers, rather than being left undefined: the Ollama picker asks on mount, and an unresolved
  // query would put every test here through a rejected promise for a control none of them is about.
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(),
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
    voice_model: "",
    voice_work_model: "",
    tiers: { weak: "a", mid: "b", top: "c" },
  },
  memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false, embed_model: "" },
  cache: { completion: false, prompt: false },
  autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
  sandbox: { mode: "local", image: "python:3.12-slim" },
  server: { token_set: false },
  mcp: { autoload: false },
  automation: { cron: true },
  guard: { chat: false },
  providers: [],
  applies: {},
};

/**
 * "Configure my right hand" was the one thing this screen could not do. Three things looked like
 * they already did it — a profile file with no reader in the API, persona memory facts retrieved by
 * keyword so a standing instruction applied only when the wording matched, and an unconditional
 * preamble slot filled by two paths the app never takes.
 */
describe("Settings — who the agent is", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
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
    vi.mocked(getInstructions).mockResolvedValue({
      name: "",
      language: "",
      instructions: "",
    } as never);
  });

  it("sends the whole identity as one record", async () => {
    const user = userEvent.setup();
    vi.mocked(putInstructions).mockResolvedValue({
      name: "Cesar",
      language: "",
      instructions: "be direct",
    } as never);
    renderWithProviders(<Settings />);

    await user.type(await screen.findByLabelText(/Standing instructions/i), "be direct");
    // Scoped: this screen has several Save buttons, and the one that matters is in this card.
    const card = screen.getByRole("region", { name: "Your agent" });
    await user.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(putInstructions).toHaveBeenCalledOnce());
    expect(vi.mocked(putInstructions).mock.calls[0][0]).toEqual({
      name: "",
      language: "",
      instructions: "be direct",
    });
  });

  it("shows what the server stored, not what was typed", async () => {
    // The free text is capped server-side. Echoing the draft would let someone paste more than the
    // budget, watch it come back, and never learn the agent is reading a prefix of it.
    const user = userEvent.setup();
    vi.mocked(putInstructions).mockResolvedValue({
      name: "",
      language: "",
      instructions: "trunc",
    } as never);
    renderWithProviders(<Settings />);

    const box = await screen.findByLabelText(/Standing instructions/i);
    await user.type(box, "truncated-far-past-the-budget");
    // Scoped: this screen has several Save buttons, and the one that matters is in this card.
    const card = screen.getByRole("region", { name: "Your agent" });
    await user.click(within(card).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(box).toHaveValue("trunc"));
  });

  it("offers the interface language in one click, without applying it behind your back", async () => {
    // Two genuinely separate choices: a Brazilian reading English documentation may want exactly
    // that split, so the app knowing the answer is not permission to decide it.
    const user = userEvent.setup();
    vi.mocked(putInstructions).mockResolvedValue({
      name: "",
      language: "English",
      instructions: "",
    } as never);
    renderWithProviders(<Settings />);

    const field = await screen.findByLabelText(/^Answer in$/i);
    expect(field).toHaveValue(""); // nothing was chosen for the user
    await user.click(screen.getByRole("button", { name: /Use interface language/i }));
    expect(field).toHaveValue("English");
  });

  it("searches for transcript width and navigates to the appearance section", async () => {
    const user = userEvent.setup();
    const onTabChange = vi.fn();
    renderWithProviders(<Settings tab="general" onTabChange={onTabChange} />);

    const search = await screen.findByRole("textbox", { name: "Search settings" });
    await user.type(search, "conversation width");
    await user.click(screen.getByRole("button", { name: /Conversation width/ }));

    expect(onTabChange).toHaveBeenCalledWith("appearance");
    expect(search).toHaveValue("");
  });

  it("finds a setting by a case-insensitive partial match in the label", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "THEME");

    const results = screen.getByRole("region", { name: "Search results" });
    expect(within(results).getByRole("button", { name: /Theme/ })).toBeInTheDocument();
    expect(within(results).queryByRole("button", { name: /Conversation width/ })).not.toBeInTheDocument();
  });

  it("finds a setting by its description, not only its label", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "light or dark");

    const results = screen.getByRole("region", { name: "Search results" });
    expect(within(results).getByRole("button", { name: /Theme/ })).toBeInTheDocument();
    expect(results.querySelector("mark")).toHaveTextContent("light or dark");
  });

  it("searches settings and opens the tab containing a result", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const search = await screen.findByRole("textbox", { name: "Search settings" });
    await user.type(search, "conversation width");
    const result = screen.getByRole("button", { name: /Conversation width/ });
    expect(result.querySelector("mark")).toHaveTextContent("Conversation width");
    await user.click(result);

    expect(await screen.findByRole("combobox", { name: "Conversation width" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Search results" })).not.toBeInTheDocument();
  });

  it("focuses the settings search with the platform shortcut", async () => {
    renderWithProviders(<Settings />);
    const search = await screen.findByRole("textbox", { name: "Search settings" });
    expect(search).not.toHaveFocus();

    await userEvent.keyboard("{Control>}k{/Control}");

    expect(search).toHaveFocus();
  });

  it("shows no-results feedback for an unmatched settings search", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.type(await screen.findByRole("textbox", { name: "Search settings" }), "zzzznot-a-settingzzzz");
    const results = await screen.findByRole("region", { name: "Search results" });
    expect(within(results).getByText(/No setting matches “zzzznot-a-settingzzzz”/)).toBeInTheDocument();
  });

  it("keeps detailed voice-model context collapsed until requested", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const learnMore = await screen.findByText("Learn more");
    const details = learnMore.closest("details");
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute("open");
    expect(details!.querySelector("p")).toBeInTheDocument();
    expect(details!.querySelector("p")).not.toBeVisible();
    await user.click(learnMore);
    expect(screen.getByText(/gemini-2\.5-flash-lite/)).toBeInTheDocument();
  });

  it("uses available width for settings rather than centering a narrow column", async () => {
    const { container } = renderWithProviders(<Settings />);

    await screen.findByRole("region", { name: "Basics" });
    expect(container.querySelector("[data-settings-scroll] > [role='tabpanel'] > div")).toHaveClass("divide-y");
    expect(container.querySelector("main .mx-auto.max-w-3xl")).toBeNull();
    expect(container.querySelector("[data-settings-scroll] > [role='tabpanel'] > div")).not.toHaveClass("mx-auto");
    expect(container.querySelector("[data-settings-scroll] > [role='tabpanel'] section")).toHaveClass("max-w-5xl");
    expect(container.querySelector(".mx-auto.max-w-7xl")).toBeNull();
  });

  it("starts General with language, default project, default model and contextual style", async () => {
    renderWithProviders(<Settings />);

    const basics = await screen.findByRole("region", { name: "Basics" });
    const order = [
      within(basics).getByRole("combobox", { name: "Language" }),
      within(basics).getByRole("textbox", { name: "Default project" }),
      within(basics).getByRole("textbox", { name: "Default model" }),
      within(basics).getByRole("link", { name: "Choose style in the composer" }),
    ];
    expect(within(basics).getByText("Chosen separately for each conversation; it isn't a global preference.")).toBeInTheDocument();
    for (let index = 0; index < order.length - 1; index += 1) {
      expect(order[index]!.compareDocumentPosition(order[index + 1]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it("puts the basic choices ahead of the dedicated voice section", async () => {
    renderWithProviders(<Settings />);

    const basics = await screen.findByRole("region", { name: "Basics" });
    const voice = screen.getByRole("heading", { name: "Voice" });
    expect(basics.compareDocumentPosition(voice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(basics).getByRole("combobox", { name: "Language" })).toBeInTheDocument();
    expect(within(basics).getByRole("textbox", { name: "Default project" })).toBeInTheDocument();
    expect(within(basics).getByRole("textbox", { name: "Default model" })).toBeInTheDocument();
    expect(within(basics).getByRole("link", { name: "Choose style in the composer" })).toBeInTheDocument();
  });

  it("says the instructions cannot grant capability, where they are written", async () => {
    // Not in a tooltip. Someone writing "you may run any command" here and then watching the agent
    // refuse deserves to have been told in the same breath.
    renderWithProviders(<Settings />);
    await screen.findByText(/cannot grant capability/i);
  });
});
