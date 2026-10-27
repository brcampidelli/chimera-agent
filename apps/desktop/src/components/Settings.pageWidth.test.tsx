import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

/** The three embedded screens are stubbed: this file is about the column they sit in, not their data. */
vi.mock("@/components/Usage", async () => {
  const { createElement } = await import("react");
  return { Usage: () => createElement("div", { "data-testid": "page-usage" }) };
});
vi.mock("@/components/Governance", async () => {
  const { createElement } = await import("react");
  return { Governance: () => createElement("div", { "data-testid": "page-security" }) };
});
vi.mock("@/components/Decisions", async () => {
  const { createElement } = await import("react");
  return { Decisions: () => createElement("div", { "data-testid": "page-decisions" }) };
});

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
  patchConfig: vi.fn(),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

const CONFIG = {
  models: {
    default: "openrouter/x", weak: "", mid: "", orchestrator: "", cost_mode: "auto", cascade: false,
    api_base: null, fallback_models: [], tiers: { weak: "a", mid: "b", top: "c" },
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
 * Usage, Security and Decisions ran to the pane's edge: they are embedded screens, which bring no
 * column, and the Settings body never supplied one for them. Appearance and Skills do; these must too.
 */
describe("Settings — the system pages keep the same column as the others", () => {
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

  it.each([
    ["Usage", "page-usage"],
    ["Security", "page-security"],
    ["Decisions", "page-decisions"],
  ])("%s sits in the same centred, gutter-padded column as Appearance", async (tabName, testId) => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await screen.findByText("a · b · c");

    await user.click(screen.getByRole("tab", { name: tabName }));
    const page = screen.getByTestId(testId);
    const column = page.parentElement!;

    // The same column Appearance draws, not a width invented for these three.
    await user.click(screen.getByRole("tab", { name: "Appearance" }));
    const appearance = screen.getByRole("combobox", { name: "Theme" }).closest(".max-w-5xl") as HTMLElement;
    expect(column.className).toBe(appearance.className);
    expect(column).toHaveClass("max-w-5xl", "px-6", "py-6");
  });

  it("lets the scrolling body shrink below its content, so a wide line cannot push past the edge", async () => {
    renderWithProviders(<Settings />);
    await screen.findByText("a · b · c");

    const body = document.querySelector("[data-settings-scroll]");
    expect(body).toHaveClass("min-w-0");
  });
});
