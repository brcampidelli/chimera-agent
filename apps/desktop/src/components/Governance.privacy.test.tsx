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
  // The screen also mounts the access card (`AccessCard.tsx`, tested on its own); an empty
  // machine keeps it out of the way of the panels this file is about.
  getAccess: vi.fn(async () => ({
    server: { bind: "127.0.0.1", port: 8765, network: false },
    server_token: { set: false },
    bridge: { enabled: false, active: false, tier: null, hint: "" },
    sharing: { enabled: true, expiry_hours: null },
    guest_door: { open: false, port: null, urls: [] },
    links: [],
  })),
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

  it("names the host a remote runtime sends to instead of calling it local", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({
        ...PRIVACY,
        routes: [{ provider: "ollama_chat", local: false, host: "ollama.com", roles: ["weak"] }],
      }),
    );
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText("leaves this machine for ollama.com")).toBeInTheDocument();
    expect(panel.queryByText("local runtime")).toBeNull();
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

  it("says the verifier's fallback reaches OpenRouter without deny on the default install", async () => {
    // Verified answers on, `local_logprob`, an OpenRouter key: the Decisions API stands behind the
    // local verifier, so `deny` must not read as covering a grounded turn's sources.
    vi.mocked(getConfig).mockResolvedValue(
      config({ ...PRIVACY, openrouter_data_collection: "deny", unscoped: ["decisions_fallback"] }),
    );
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText(/falls back to OpenRouter's Decisions API .* does not carry this preference/i))
      .toBeInTheDocument();
    expect(panel.queryByText(/The Decisions API backend \(System One\)/)).toBeNull();
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

  it("says telemetry asked for without the [otel] extra exports nothing", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config({ ...PRIVACY, telemetry: false, telemetry_requested: true }),
    );
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText(/\[otel\] extra is not installed: nothing is exported/)).toBeInTheDocument();
    expect(panel.queryByText(/OpenTelemetry export is on/)).toBeNull();
  });

  it("warns only about a bot that can start; one never set up reads as not connected", async () => {
    vi.mocked(getConfig).mockResolvedValue(
      config(PRIVACY, {
        messaging: {
          allowed_users: { discord: [], slack: [], telegram: [] },
          configured: ["discord"],
        },
      }),
    );
    renderWithProviders(<Governance />);
    const panel = within(await card());
    expect(panel.getByText("discord: anyone")).toBeInTheDocument();
    expect(panel.getByText("slack: not connected")).toBeInTheDocument();
    expect(panel.getByText("telegram: not connected")).toBeInTheDocument();
    expect(panel.queryByText("slack: anyone")).toBeNull();
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
