import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessagingCard, Settings } from "@/components/Settings";
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

const LISTEN = DICTS.en["settings.row.voiceListen"];
const REPLY = DICTS.en["settings.row.voiceReply"];

/** The two voice switches on the Discord card save their own variables and show their saved state. */
describe("MessagingCard — the voice switches", () => {
  it("saves CHIMERA_CHAT_INBOUND_MEDIA and CHIMERA_CHAT_VOICE_REPLY from its own switches", async () => {
    const user = userEvent.setup();
    const save = vi.fn();
    vi.mocked(getMessaging).mockResolvedValue({
      discord: { configured: true, running: true, error: null },
    } as never);
    renderWithProviders(<MessagingCard save={save} allowed={["42"]} />);

    const listen = await screen.findByRole("switch", { name: LISTEN });
    expect(listen).toHaveAttribute("aria-checked", "false");
    await user.click(listen);
    expect(save).toHaveBeenLastCalledWith({ CHIMERA_CHAT_INBOUND_MEDIA: "true" });

    await user.click(screen.getByRole("switch", { name: REPLY }));
    expect(save).toHaveBeenLastCalledWith({ CHIMERA_CHAT_VOICE_REPLY: "true" });
  });

  it("shows the switches on as saved", async () => {
    vi.mocked(getMessaging).mockResolvedValue({
      telegram: { configured: true, running: true, error: null },
    } as never);
    renderWithProviders(
      <MessagingCard save={vi.fn()} platform="telegram" tokenEnv="CHIMERA_TELEGRAM_BOT_TOKEN" listen speak />,
    );
    expect(await screen.findByRole("switch", { name: LISTEN })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("switch", { name: REPLY })).toHaveAttribute("aria-checked", "true");
  });
});

describe("MessagingCard — the owner's voice model", () => {
  it("saves the typed path from its own field, and says Chimera ships no voice", async () => {
    const user = userEvent.setup();
    const save = vi.fn();
    vi.mocked(getMessaging).mockResolvedValue({
      discord: { configured: true, running: true, error: null },
    } as never);
    renderWithProviders(<MessagingCard save={save} allowed={["42"]} />);
    const field = await screen.findByRole("textbox", { name: DICTS.en["settings.row.chatVoiceModel"] });
    await user.type(field, "/voices/me.onnx");
    await user.click(within(field.parentElement as HTMLElement).getByRole("button", { name: /^Save$/ }));
    expect(save).toHaveBeenLastCalledWith({ CHIMERA_CHAT_VOICE_MODEL: "/voices/me.onnx" });
    expect(DICTS.en["settings.hint.chatVoiceModel"]).toMatch(/ships no voice/);
  });
});

const VOICE_KEYS = [
  "settings.row.chatVoiceModel",
  "settings.hint.chatVoiceModel",
  "settings.row.voiceListen",
  "settings.hint.voiceListen",
  "settings.row.voiceReply",
  "settings.hint.voiceReply",
  "settings.search.terms.voice",
] as const;

describe("voice strings", () => {
  it("are present, non-empty, in all ten interface languages", () => {
    const langs = Object.keys(DICTS) as Lang[];
    expect(langs).toHaveLength(10);
    for (const lang of langs) {
      for (const key of VOICE_KEYS) {
        expect(DICTS[lang][key], `${lang} ${key}`).toBeTruthy();
      }
    }
  });
});

describe("Settings search — the voice switches", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getConfig).mockResolvedValue({
      models: { default: "openrouter/x", weak: "", mid: "", orchestrator: "", cost_mode: "auto", cascade: false, api_base: null, fallback_models: [], tiers: { weak: "a", mid: "b", top: "c" } },
      memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false },
      cache: { completion: false, prompt: false },
      autonomy: { reach: "", approval: "", host_exec: "ask", denied_tools: [], pull_requests: false, close_loop: false },
      sandbox: { mode: "local", image: "python:3.12-slim" },
      server: { token_set: false },
      mcp: { autoload: false },
      automation: { cron: true },
      guard: { chat: false },
      providers: [],
      applies: {},
    } as never);
    vi.mocked(getDoctor).mockResolvedValue({ has_any_key: true, local_model: false, can_answer: true, configured_providers: ["openrouter"], default_model: "openrouter/x", tiers: { weak: "a", mid: "b", top: "c" }, memory_backend: "json", cache: false, sandbox: "local" } as never);
    vi.mocked(getMessaging).mockResolvedValue({} as never);
    vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "", instructions: "" } as never);
  });

  afterEach(() => localStorage.removeItem("chimera.lang"));

  it("finds the listen switch by 'voice note' and focuses it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);
    await user.type(await screen.findByRole("textbox", { name: DICTS.en["settings.search.label"] }), "voice note");
    await user.click(await screen.findByRole("button", { name: (name) => name.includes(LISTEN) }));
    // Both bot cards carry the switch under the same label; the search lands on the Discord one.
    expect(document.getElementById("settings-voice-listen-discord")).toHaveFocus();
  }, 20_000);
});
