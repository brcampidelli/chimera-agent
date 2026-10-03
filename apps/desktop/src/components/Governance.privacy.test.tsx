/** The Security screen says who receives a prompt and what may be kept (study 29, P5.6).
 *
 * The answer was spread over a dozen model rows on Settings, the OpenRouter route's data policy had
 * no setting at all, and the Decisions API backend reached OpenRouter outside the gateway without a
 * word. The card is read-only: every line is a fact the server reported, and the controls stay where
 * they already are.
 */

import { screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Governance } from "@/components/Governance";
import { getConfig, getGovernanceAudit, getGovernanceInjection, getSandboxState } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getConfig: vi.fn(),
  getGovernanceInjection: vi.fn(),
  getApprovals: vi.fn(async () => []),
  getGovernanceAudit: vi.fn(),
  getSandboxState: vi.fn(),
}));

const SECRET_HINT = "…9f2c";

function config(privacy: Record<string, unknown> | undefined, over: Record<string, unknown> = {}) {
  return {
    models: { api_base: null },
    memory: {
      backend: "sqlite",
      semantic: false,
      auto_consolidate: false,
      remember_from_chat: true,
      embed_model: "openrouter/openai/text-embedding-3-small",
    },
    autonomy: { governance: "off", egress_allow: [] },
    messaging: { allowed_users: { discord: ["123"], telegram: [] } },
    providers: [{ env: "OPENROUTER_API_KEY", name: "openrouter", label: "OpenRouter", set: true, hint: SECRET_HINT }],
    ...(privacy ? { privacy } : {}),
    ...over,
  } as never;
}

const PRIVACY = {
  openrouter_data_collection: "allow",
  openrouter_zdr: false,
  routes: [
    { provider: "openrouter", local: false, roles: ["default", "fusion_judge"] },
    { provider: "ollama_chat", local: true, roles: ["weak"] },
  ],
  telemetry: false,
  unscoped: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getGovernanceInjection).mockResolvedValue({
    total_attacks: 0,
    defended_asr: 0,
    undefended_asr: 0,
    defended_block_rate: 0,
    undefended_block_rate: 0,
    by_category: [],
    attacks: [],
    leaks_defended: [],
    defense: "taint_narrowing",
    armed: true,
    trust_kernel: false,
    over_block_rate: 0,
    over_block_with_approver: 0,
    legitimate_tasks: 0,
    questions_asked: 0,
    pending_questions: 0,
  } as never);
  vi.mocked(getGovernanceAudit).mockResolvedValue({ events: [], count: 0, populated: false } as never);
  vi.mocked(getSandboxState).mockResolvedValue({
    configured: "auto", backend: "host", isolated: false, reason: "", platform: "Windows",
  } as never);
});

async function card(): Promise<HTMLElement> {
  const title = await screen.findByRole("heading", { name: "Privacy" });
  return title.closest("section") as HTMLElement;
}

describe("Governance — the privacy card", () => {
  it("names each provider that receives prompts, and which of them stay local", async () => {
    vi.mocked(getConfig).mockResolvedValue(config(PRIVACY));
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText("openrouter")).toBeInTheDocument();
    expect(panel.getByText("default, fusion_judge")).toBeInTheDocument();
    expect(panel.getByText("leaves this machine")).toBeInTheDocument();
    expect(panel.getByText("local runtime")).toBeInTheDocument();
  });

  it("says the OpenRouter route keeps what its own policy allows while nothing is set", async () => {
    vi.mocked(getConfig).mockResolvedValue(config(PRIVACY));
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText(/under that provider's own data policy/i)).toBeInTheDocument();
    expect(panel.queryByText(/without this preference/i)).toBeNull();
  });

  it("says deny is in force, and names the surface it does not cover", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ ...PRIVACY, openrouter_data_collection: "deny", openrouter_zdr: true, unscoped: ["decisions"] }),
    );
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText(/neither store nor train on prompts/i)).toBeInTheDocument();
    expect(panel.getByText(/Zero-data-retention endpoints only/i)).toBeInTheDocument();
    expect(panel.getByText(/Decisions API backend .* without this preference/i)).toBeInTheDocument();
  });

  it("names the custom endpoint every call goes to", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config(PRIVACY, { models: { api_base: "http://10.0.0.5:8000/v1" } }),
    );
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText(/every call goes to http:\/\/10\.0\.0\.5:8000\/v1/)).toBeInTheDocument();
  });

  it("reports memory, telemetry, the declared hosts and who may talk to each bot", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config(
        { ...PRIVACY, telemetry: true },
        {
          memory: {
            backend: "sqlite", semantic: true, auto_consolidate: false, remember_from_chat: false,
            embed_model: "gemini/text-embedding-004",
          },
          autonomy: { governance: "off", egress_allow: ["api.github.com"] },
        },
      ),
    );
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText("Remember from chat: off")).toBeInTheDocument();
    expect(panel.getByText(/sent to gemini\/text-embedding-004/)).toBeInTheDocument();
    expect(panel.getByText(/OpenTelemetry export is on/)).toBeInTheDocument();
    expect(panel.getByText("api.github.com")).toBeInTheDocument();
    expect(panel.getByText("discord: 1 listed")).toBeInTheDocument();
    expect(panel.getByText("telegram: anyone")).toBeInTheDocument();
  });

  it("never shows a credential, not even the hint the API-keys card uses", async () => {
    vi.mocked(getConfig).mockResolvedValue(config(PRIVACY));
    renderWithProviders(<Governance />);
    const section = await card();
    expect(section.textContent ?? "").not.toContain(SECRET_HINT);
  });

  it("is absent when the server does not report the block", async () => {
    // An empty card would read as "nobody receives your prompts".
    vi.mocked(getConfig).mockResolvedValue(config(undefined));
    renderWithProviders(<Governance />);
    // That line renders only once the config has arrived, so the absence below is not a race.
    await screen.findByText(/The trust kernel is off/);
    expect(screen.queryByRole("heading", { name: "Privacy" })).toBeNull();
  });
});
