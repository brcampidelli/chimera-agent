import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDoctor,
  getInstructions,
  getMessaging,
  getOllamaModels,
  patchConfig,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getOllamaModels: vi.fn(),
  patchConfig: vi.fn(async () => ({ updated: [] })),
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
    ollama_base_url: "http://localhost:11434",
    complete_model: "",
  },
  memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
  cache: { completion: false, prompt: false },
  autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [] },
  sandbox: { mode: "local", image: "python:3.12-slim" },
  browser: { headless: true },
  server: { token_set: false },
  mcp: { autoload: false },
  automation: { cron: true },
  guard: { chat: false },
  providers: [],
  applies: {},
  pinned: [],
};

/**
 * Every model field on this screen that names an Ollama tag was a memory test, and a wrong tag does
 * not fail at save time — it fails on the first call, mid-run, as a 404 from a server the user
 * believed was ready.
 *
 * The assertions that matter are the two about NOTHING. A picker rendered from an empty list says
 * *you have no models*, and that sentence is true in one of the two cases that produce it and a
 * fabrication in the other. So the unreachable case names the URL that did not answer, and the empty
 * case says the server answered — two different remedies, never one blank dropdown.
 */
describe("Settings — the models this machine actually has", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(CONFIG as never);
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
    vi.mocked(getInstructions).mockResolvedValue(
      { name: "", language: "", instructions: "" } as never,
    );
  });

  const picker = () => screen.findByRole("combobox", { name: "Installed models" });

  it("offers what is pulled, and writes the slug the gateway expects", async () => {
    const user = userEvent.setup();
    vi.mocked(getOllamaModels).mockResolvedValue({
      base_url: "http://localhost:11434",
      reachable: true,
      models: ["llama3:latest", "qwen2.5-coder:1.5b-base"],
      reason: "",
    } as never);
    renderWithProviders(<Settings />);

    await user.selectOptions(await picker(), "llama3:latest");

    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    // `ollama/` prefixed here rather than left to the user: the tag is what `ollama list` prints and
    // the slug is what LiteLLM routes on, and the gap between them is a support question.
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_DEFAULT_MODEL: "ollama/llama3:latest",
    });
  });

  it("says nothing answered, instead of showing an empty list", async () => {
    // The failure this whole feature is built around. An empty picker is a claim about the user's
    // model library; when nothing answered the door we have no basis for one.
    vi.mocked(getOllamaModels).mockResolvedValue({
      base_url: "http://localhost:11434",
      reachable: false,
      models: [],
      reason: "unreachable",
    } as never);
    renderWithProviders(<Settings />);

    await screen.findByText(/Nothing answered at http:\/\/localhost:11434/i);
    expect(screen.queryByRole("combobox", { name: "Installed models" })).not.toBeInTheDocument();
  });

  it("distinguishes a server with nothing pulled from a server that is not there", async () => {
    // Same zero options, opposite remedies: one wants `ollama pull`, the other wants a daemon.
    vi.mocked(getOllamaModels).mockResolvedValue({
      base_url: "http://localhost:11434",
      reachable: true,
      models: [],
      reason: "",
    } as never);
    renderWithProviders(<Settings />);

    await screen.findByText(/has nothing pulled yet/i);
    expect(screen.queryByText(/Nothing answered at/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Installed models" })).not.toBeInTheDocument();
  });

  it("shows the configured default as selected when it is one of these models", async () => {
    vi.mocked(getConfig).mockResolvedValue({
      ...CONFIG,
      models: { ...CONFIG.models, default: "ollama/llama3:latest" },
    } as never);
    vi.mocked(getOllamaModels).mockResolvedValue({
      base_url: "http://localhost:11434",
      reachable: true,
      models: ["llama3:latest", "qwen2.5-coder:1.5b-base"],
      reason: "",
    } as never);
    renderWithProviders(<Settings />);

    expect(await picker()).toHaveValue("llama3:latest");
  });

  it("shows nothing selected when the default is a cloud model", async () => {
    // A cloud default rendered as a local tag would be the picker asserting something about the
    // agent that is not true — and the fix for it would be to change a setting that was already right.
    vi.mocked(getOllamaModels).mockResolvedValue({
      base_url: "http://localhost:11434",
      reachable: true,
      models: ["llama3:latest"],
      reason: "",
    } as never);
    renderWithProviders(<Settings />);

    expect(await picker()).toHaveValue("");
  });
});
