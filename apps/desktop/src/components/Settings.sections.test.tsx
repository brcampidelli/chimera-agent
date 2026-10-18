import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SETTINGS_TABS, Settings, settingsTab } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  // Settings shows the inline-suggestion acceptance rate now.
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
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
    tiers: { weak: "a", mid: "b", top: "c" },
  },
  memory: {
    backend: "json",
    semantic: false,
    auto_consolidate: false,
    remember_from_chat: false,
  },
  cache: { completion: false, prompt: false },
  autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
  sandbox: { mode: "local", image: "python:3.12-slim" },
  server: { token_set: false },
  mcp: { autoload: false },
  automation: { cron: true },
  guard: { chat: false },
  providers: [],
  // Mirrors `config_api.APPLIES_WHEN`. It has to: these tests assert that the screen renders exactly
  // what the server declares, and a fixture that has drifted from the declaration would keep passing
  // while the real screen said something else.
  applies: {
    CHIMERA_APP_CRON: "next_launch",
    CHIMERA_MCP_AUTOLOAD: "next_launch",
    CHIMERA_CASCADE: "next_conversation",
    CHIMERA_GUARD_CHAT: "next_conversation",
    CHIMERA_CHAT_MEMORY: "next_conversation",
    CHIMERA_BROWSER_HEADLESS: "next_conversation",
  },
};

/**
 * Settings as a document with a table of contents: six sections in a column, Appearance its own
 * section rather than the first card of General, and the rows most people never change folded under
 * "Advanced" — still all there, one click away.
 */
describe("Settings — sections", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true, local_model: false, can_answer: true, configured_providers: ["openrouter"],
      default_model: "openrouter/x", tiers: { weak: "a", mid: "b", top: "c" }, memory_backend: "json",
      cache: false, sandbox: "local",
    } as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "", instructions: "" } as never);
  });

  it("gives Appearance its own section, and an address", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await screen.findByText("a · b · c");
    // General no longer starts with how the app looks.
    expect(screen.queryByRole("combobox", { name: "Theme" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Appearance" }));
    expect(screen.getByRole("combobox", { name: "Theme" })).toBeInTheDocument();
    expect(settingsTab("appearance")).toBe("appearance");
  });

  it("folds the rarely changed rows under Advanced, closed", async () => {
    renderWithProviders(<Settings />);
    const summary = await screen.findByText("Advanced");
    const details = summary.closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    // Folded, not removed.
    expect(details.textContent).toMatch(/Memory/);
  });

  it("lists its sections in a column a keyboard moves through with the arrows", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await screen.findByText("a · b · c");
    const list = screen.getByRole("tablist", { name: "Settings" });
    expect(list).toHaveAttribute("aria-orientation", "vertical");
    screen.getByRole("tab", { name: "General" }).focus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getByRole("tab", { name: "Appearance" })).toHaveAttribute("aria-selected", "true");
  });

  it("groups what you add under Personalization and what you set once under System", async () => {
    renderWithProviders(<Settings />);
    await screen.findByText("a · b · c");
    const list = screen.getByRole("tablist", { name: "Settings" });
    // The column, top to bottom: tabs and the two group headings, in the order they are drawn.
    const column = Array.from(list.children).map((el) =>
      el.getAttribute("role") === "tab" ? el.textContent : `[${el.textContent}]`,
    );
    expect(column).toEqual([
      "General",
      "Appearance",
      "[Personalization]",
      "Skills",
      "Connectors",
      "Plugins",
      "[System]",
      "Connections",
      "Usage",
      "Security",
      "Decisions",
    ]);
    // The headings are not tabs, so a screen reader still counts nine.
    expect(screen.getAllByRole("tab")).toHaveLength(SETTINGS_TABS.length);
  });

  it("gives Skills and Connectors addresses, and takes the old sub-tab names as aliases", () => {
    expect(settingsTab("skills")).toBe("skills");
    expect(settingsTab("connectors")).toBe("connectors");
    expect(settingsTab("plugins")).toBe("plugins");
    // MCP and OpenAPI were Connections' sub-tabs and never had an address; typed by hand they land
    // on the tab that holds them now.
    expect(settingsTab("mcp")).toBe("connectors");
    expect(settingsTab("openapi")).toBe("connectors");
    // A stale value still lands on the screen, never on nothing.
    expect(settingsTab("nonsense")).toBe("general");
    expect(settingsTab("toString")).toBe("general");
  });
});
