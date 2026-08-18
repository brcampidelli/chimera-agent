import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  patchConfig,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  // Answers, rather than being left undefined: the Ollama picker asks on mount, and an unresolved
  // query would put every test here through a rejected promise for a control none of them is about.
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
      fallback_models: ["openrouter/a", "openrouter/b"],
      tiers: { weak: "a", mid: "b", top: "c" },
    },
    memory: {
      backend: "json",
      semantic: false,
      auto_consolidate: false,
      remember_from_chat: false,
      skill_cards: false,
    },
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
 * Eight settings were already writable through `PATCH /api/config`, already returned by
 * `GET /api/config`, already in the generated client types — and had no control anywhere. The ninth,
 * skill cards, is the one that matters: with it off the agent extracts a skill from every successful
 * run and never reads one back, which is the product's central claim with the wire cut.
 */
describe("Settings — the controls that were one row away", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true,
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

  async function save(user: ReturnType<typeof userEvent.setup>, label: RegExp, value: string) {
    const field = await screen.findByRole("textbox", { name: label });
    await user.clear(field);
    await user.type(field, value);
    await user.click(
      within(field.closest("div")?.parentElement as HTMLElement).getByRole("button", {
        name: "Save",
      }),
    );
  }

  it("pins a rung of the ladder", async () => {
    // The Status card has always SHOWN the resolved ladder. Someone who wanted cheap-on-easy and
    // strong-on-hard could read exactly what they were getting and change none of it.
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await save(user, /^Weak rung$/, "openrouter/cheap");
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_WEAK_MODEL: "openrouter/cheap",
    });
  });

  it("points the agent at a local server", async () => {
    // The difference between "supports local models" and "supports local models if you edit .env".
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await save(user, /^Custom endpoint$/, "http://localhost:11434/v1");
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_API_BASE: "http://localhost:11434/v1",
    });
  });

  it("shows the fallback chain as it is stored, and saves what was typed", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    const field = await screen.findByRole("textbox", { name: /^Fallback models$/ });
    expect(field).toHaveValue("openrouter/a, openrouter/b"); // a list, rendered as one

    await save(user, /^Fallback models$/, "openrouter/c");
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_FALLBACK_MODELS: "openrouter/c",
    });
  });

  it("connects what the agent learns to what it uses", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: "Use what it learned" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_SKILL_CARDS: "true" });
  });

  it("lets you watch the page the agent is browsing", async () => {
    // `CHIMERA_BROWSER_HEADLESS` was wired to the browser tool from the day it shipped and refused
    // by `PATCH /api/config`, so the only way to see what the agent was doing on a web page was to
    // edit `.env` by hand and restart — a file the app never mentions.
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await user.click(await screen.findByRole("switch", { name: "Show the browser window" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    // Inverted, because the setting is `headless` and the control is named for what turning it ON
    // does. Sending "true" here would open a window by asking for the state that has none.
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_BROWSER_HEADLESS: "false" });
  });

  it("reads as off when the server does not mention the browser at all", async () => {
    // A server one release behind sends no `browser` block, and the switch has to default to the
    // shipped behaviour. Defaulting the other way would show a window that is not going to open.
    renderWithProviders(<Settings />);
    expect(await screen.findByRole("switch", { name: "Show the browser window" })).not.toBeChecked();
  });

  it("reads as on once the browser is headful", async () => {
    vi.mocked(getConfig).mockResolvedValue(config({ browser: { headless: false } }) as never);
    renderWithProviders(<Settings />);
    expect(await screen.findByRole("switch", { name: "Show the browser window" })).toBeChecked();
  });

  it("hides the container image until there is a container", async () => {
    // A field that changes nothing is a field someone will change and then wonder about.
    renderWithProviders(<Settings />);
    await screen.findByRole("switch", { name: "Use what it learned" });
    expect(screen.queryByRole("textbox", { name: /^Container image$/ })).not.toBeInTheDocument();

    vi.mocked(getConfig).mockResolvedValue(
      config({ sandbox: { mode: "docker", image: "python:3.12-slim" } }) as never,
    );
    renderWithProviders(<Settings />);
    expect(await screen.findByRole("textbox", { name: /^Container image$/ })).toBeInTheDocument();
  });
});
