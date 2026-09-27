import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Settings } from "@/components/Settings";
import {
  getConfig,
  getDecisionModels,
  getDoctor,
  getInstructions,
  getMessaging,
  patchConfig,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDecisionModels: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
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

function row(slug: string, over: Record<string, unknown> = {}) {
  return {
    slug,
    name: `Vendor: ${slug}`,
    input_per_m: 0.042,
    context: 32000,
    description: `${slug} is a decision model.`,
    contract: "jev",
    questions: ["noul", "choice", "score"],
    alias: false,
    selectable: true,
    refusal: "",
    calibrated: false,
    ...over,
  };
}

function listing(over: Record<string, unknown> = {}) {
  return {
    backend: "openrouter_decisions",
    model: "",
    default_model: "typesafe/jev-1.13",
    backends: ["local_logprob", "hosted_verbalized", "openrouter_decisions"],
    models: [
      row("respan/span-01", {
        contract: "behavior", questions: ["noul"], selectable: false, refusal: "behavior_contract", context: null,
      }),
      row("jaredpalmer/kev-4b", { context: 8192 }),
      row("~typesafe/jev-latest", { alias: true, selectable: false, refusal: "alias" }),
      row("typesafe/jev-1.13"),
    ],
    stale: false,
    reason: "",
    openrouter_key_set: true,
    ...over,
  };
}

const onOpenRouter = (model = "") =>
  config({ decisions: { backend: "openrouter_decisions", model } });

async function card() {
  return screen.findByRole("region", { name: "System One" });
}

/**
 * The System One card: which backend answers a typed decision, and — on OpenRouter — which model,
 * with what each row claims (price, context, calibrated or not) and the ones listed but refused.
 */
describe("Settings — the System One card", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue(config() as never);
    vi.mocked(getDecisionModels).mockResolvedValue(listing() as never);
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

  it("reads a server without the block as local, and asks no list for it", async () => {
    renderWithProviders(<Settings />);
    const region = await card();

    expect(within(region).getByRole("radio", { name: /^Local/ })).toBeChecked();
    expect(within(region).getByRole("radio", { name: /^OpenRouter System One/ })).not.toBeChecked();
    expect(region).toHaveTextContent("On a default install it is asked on its own in one place");
    expect(within(region).queryByLabelText("Model")).toBeNull();
    expect(getDecisionModels).not.toHaveBeenCalled();
  });

  it("saves a backend with its model emptied, so no slug outlives the backend it was for", async () => {
    vi.mocked(getConfig).mockResolvedValue(onOpenRouter("jaredpalmer/kev-4b") as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const region = await card();

    await user.click(within(region).getByRole("radio", { name: /^Hosted judge/ }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_DECISION_BACKEND: "hosted_verbalized",
      CHIMERA_DECISION_MODEL: "",
    });
  });

  it("on OpenRouter offers only the choosable models, and says why the others are not", async () => {
    vi.mocked(getConfig).mockResolvedValue(onOpenRouter() as never);
    renderWithProviders(<Settings />);
    const region = await card();

    const select = await within(region).findByLabelText("Model");
    const options = within(select).getAllByRole("option").map((o) => o.getAttribute("value"));
    expect(options).toEqual(["jaredpalmer/kev-4b", "typesafe/jev-1.13"]);
    expect(select).toHaveValue("typesafe/jev-1.13"); // empty model = the default, shown as such
    expect(region).toHaveTextContent("Listed, not choosable:");
    expect(region).toHaveTextContent("respan/span-01 — behaviour scoring, not the yes/no, choice and score contract Chimera sends");
    expect(region).toHaveTextContent("~typesafe/jev-latest — a moving alias");
  });

  it("marks an uncalibrated model and says its confidence reads raw", async () => {
    vi.mocked(getConfig).mockResolvedValue(onOpenRouter() as never);
    renderWithProviders(<Settings />);
    const region = await card();

    await within(region).findByLabelText("Model");
    expect(region).toHaveTextContent("$0.042/1M in");
    expect(region).toHaveTextContent("32k context");
    expect(within(region).getAllByText("uncalibrated").length).toBeGreaterThan(0);
    expect(region).toHaveTextContent("No calibration map for this model yet: its confidence is read raw.");
  });

  it("drops the raw-confidence line for a model a map calibrates", async () => {
    vi.mocked(getConfig).mockResolvedValue(onOpenRouter("jaredpalmer/kev-4b") as never);
    vi.mocked(getDecisionModels).mockResolvedValue(
      listing({ models: [row("jaredpalmer/kev-4b", { calibrated: true, context: 8192 }), row("typesafe/jev-1.13")] }) as never,
    );
    renderWithProviders(<Settings />);
    const region = await card();

    const select = await within(region).findByLabelText("Model");
    expect(select).toHaveValue("jaredpalmer/kev-4b");
    expect(within(region).getByText("calibrated")).toBeInTheDocument();
    expect(region).not.toHaveTextContent("its confidence is read raw");
  });

  it("saves a picked model together with the backend", async () => {
    vi.mocked(getConfig).mockResolvedValue(onOpenRouter() as never);
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const region = await card();

    await user.selectOptions(await within(region).findByLabelText("Model"), "jaredpalmer/kev-4b");
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({
      CHIMERA_DECISION_BACKEND: "openrouter_decisions",
      CHIMERA_DECISION_MODEL: "jaredpalmer/kev-4b",
    });
  });

  it("shows the answer check on by default, with what it measured and what runs without Ollama", async () => {
    renderWithProviders(<Settings />);
    const region = await card();

    const toggle = within(region).getByRole("switch", { name: "Verify answers grounded in sources" });
    expect(toggle).toHaveAttribute("aria-checked", "true"); // a server without the field is on the default
    expect(region).toHaveTextContent("a third fewer wrong answers (33 → 21, none made worse)");
    expect(region).toHaveTextContent("about 1.9× the cost with the local verifier");
    expect(region).toHaveTextContent(
      "If the local model isn't running: Jev (typesafe/jev-1.13) when an OpenRouter key is set, otherwise the old lexical check.",
    );
  });

  it("turns the answer check off through the setting", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    const region = await card();

    await user.click(within(region).getByRole("switch", { name: "Verify answers grounded in sources" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalledOnce());
    expect(vi.mocked(patchConfig).mock.calls[0][0]).toEqual({ CHIMERA_VERIFIED_ANSWERS: "false" });
  });

  it("draws the answer check off when the server reports it off", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ decisions: { backend: "local_logprob", model: "", verified_answers: false } }) as never,
    );
    renderWithProviders(<Settings />);
    const region = await card();

    expect(within(region).getByRole("switch", { name: "Verify answers grounded in sources" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("says when the list is the offline default and when no key is set", async () => {
    vi.mocked(getConfig).mockResolvedValue(onOpenRouter() as never);
    vi.mocked(getDecisionModels).mockResolvedValue(
      listing({
        models: [row("typesafe/jev-1.13", { input_per_m: null, context: null, description: "" })],
        stale: true,
        reason: "unreachable",
        openrouter_key_set: false,
      }) as never,
    );
    renderWithProviders(<Settings />);
    const region = await card();

    await within(region).findByLabelText("Model");
    expect(region).toHaveTextContent("OpenRouter's list could not be reached; only the default is shown.");
    expect(region).toHaveTextContent("No OpenRouter key is set: every decision will halt until one is.");
    expect(region).toHaveTextContent("price not quoted");
  });
});
