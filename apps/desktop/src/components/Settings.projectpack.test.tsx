import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging, patchConfig } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDeferSaving: vi.fn(async () => ({ builtin: null, mcp: null, mcp_state: "autoload_off" })),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

const DOCTOR = {
  has_any_key: true,
  local_model: false,
  can_answer: true,
  configured_providers: ["openrouter"],
  default_model: "openrouter/x",
  tiers: { weak: "a", mid: "b", top: "c" },
  memory_backend: "json",
  cache: false,
  sandbox: "local",
};

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
    ...over,
  };
}

const LABEL = "Project packs";

/** Study 29, P7.6: the project-pack switch is a row on the screen, off by default. */
describe("Settings — project packs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getDoctor).mockResolvedValue(DOCTOR as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "", instructions: "" } as never);
  });

  it("is off on a server that predates the block, and says it only narrows and is unmeasured", async () => {
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Experimental" });

    expect(within(card).getByRole("switch", { name: LABEL })).not.toBeChecked();
    expect(card).toHaveTextContent("It only removes");
    expect(card).toHaveTextContent("bench/project_pack and has not run");
  });

  it("saves the owner's choice under its own variable", async () => {
    const user = userEvent.setup();
    vi.mocked(getConfig).mockResolvedValue(config({ project_pack: { enabled: true } }) as never);
    renderWithProviders(<Settings />);
    const card = await screen.findByRole("region", { name: "Experimental" });

    const toggle = within(card).getByRole("switch", { name: LABEL });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_PROJECT_PACK: "false" });
  });
});
