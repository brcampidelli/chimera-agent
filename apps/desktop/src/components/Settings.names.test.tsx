import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Settings } from "@/components/Settings";
import { getConfig, getDoctor, getInstructions, getMessaging } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getCompletionStats: vi.fn(async () => ({ accepted: 0, dismissed: 0, rate: null, mean_ms: null })),
  getConfig: vi.fn(),
  getDoctor: vi.fn(),
  getInstructions: vi.fn(),
  getMessaging: vi.fn(),
  getOllamaModels: vi.fn(async () => ({ base_url: "", reachable: false, models: [], reason: "no_url" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  putInstructions: vi.fn(),
  startMessaging: vi.fn(),
  stopMessaging: vi.fn(),
}));

/**
 * Every control on this screen says what it is FOR, not just what is in it.
 *
 * Walking the running app turned up a dozen inputs with no accessible name. Buttons and tabs were
 * all fine; form controls were not. They had placeholders, which is not the same thing — a
 * placeholder disappears the moment the field has content, and screen readers differ on whether
 * they announce one at all.
 *
 * The worst case was here: **five password fields rendering at once**, every one announcing "cole a
 * chave…", with nothing saying which provider it belonged to. On the one screen where pasting the
 * wrong secret into the wrong field is a real and silent error.
 *
 * Rendered rather than read from source, because the accessible name has several sources — an
 * `aria-label`, a `<label for>`, a wrapping `<label>` — and a source scan that only looked for the
 * first would have accused every checkbox on the screen of a defect it does not have. Testing
 * Library computes the name the way a screen reader does, which is the only definition that counts.
 */

const PROVIDERS = [
  { env: "OPENROUTER_API_KEY", label: "OpenRouter", hint: "sk-or-…", keys_url: "", llm: true, name: "openrouter", set: false, model: "" },
  { env: "ANTHROPIC_API_KEY", label: "Anthropic", hint: "sk-ant-…", keys_url: "", llm: true, name: "anthropic", set: false, model: "" },
  { env: "TAVILY_API_KEY", label: "Tavily", hint: "tvly-…", keys_url: "", llm: false, name: "tavily", set: false, model: "" },
];

function config() {
  return {
    models: { default: "openrouter/x", weak: "", mid: "", orchestrator: "", cost_mode: "auto", cascade: false, api_base: null, fallback_models: [], complete_model: "", ollama_base_url: "", tiers: { weak: "a", mid: "b", top: "c" } },
    memory: { backend: "json", semantic: false, auto_consolidate: false, remember_from_chat: false, skill_cards: false, embed_model: "" },
    cache: { completion: false, prompt: false },
    autonomy: {
    reach: "",
    approval: "",
    host_exec: "ask",
    denied_tools: [],
    governance: "off",
    approval_webhook_set: false,
  },
    sandbox: { mode: "local", image: "python:3.12-slim" },
    server: { token_set: false },
    mcp: { autoload: false },
    automation: { cron: true },
    guard: { chat: false },
    providers: PROVIDERS,
    applies: {},
  };
}

/** Controls the accessibility tree gives no name to. */
function anonymous(): string[] {
  const out: string[] = [];
  for (const el of document.querySelectorAll("input, select, textarea")) {
    const control = el as HTMLInputElement;
    if (control.type === "hidden") continue;
    const labelled =
      control.getAttribute("aria-label")?.trim() ||
      control.getAttribute("aria-labelledby") ||
      (control.id && document.querySelector(`label[for="${control.id}"]`)) ||
      control.closest("label");
    if (!labelled) out.push(`<${control.tagName.toLowerCase()} placeholder="${control.placeholder ?? ""}">`);
  }
  return out;
}

describe("Settings — every control has a name", () => {
  beforeEach(() => {
    vi.mocked(getConfig).mockResolvedValue(config() as Awaited<ReturnType<typeof getConfig>>);
    vi.mocked(getDoctor).mockResolvedValue({
      has_any_key: true, configured_providers: ["openrouter"], default_model: "openrouter/x",
      tiers: { weak: "a", mid: "b", top: "c" }, memory_backend: "json", cache: true,
    } as Awaited<ReturnType<typeof getDoctor>>);
    vi.mocked(getInstructions).mockResolvedValue({ instructions: "", language: "", name: "" });
    vi.mocked(getMessaging).mockResolvedValue({} as Awaited<ReturnType<typeof getMessaging>>);
  });

  it("names the controls on the general tab", async () => {
    renderWithProviders(<Settings />);
    await screen.findByText(/Appearance|Aparência/i);

    expect(anonymous(), "unnamed controls").toEqual([]);
  });

  it("names each API key field after its provider", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Settings />);

    await screen.findByText(/Appearance|Aparência/i);
    // The keys live on the GENERAL tab, and each renders a button until it is opened. Opening them
    // all is what puts the identical password fields on screen at once — the state the defect lived
    // in, and the reason "which one is this?" mattered.
    for (const b of await screen.findAllByRole("button", { name: /^Set$|^Definir$/i })) await user.click(b);

    expect(anonymous(), "unnamed controls").toEqual([]);
    for (const p of PROVIDERS) {
      expect(screen.getByLabelText(p.label), `no field named for ${p.label}`).toBeTruthy();
    }
  });

  it("would notice if a name were removed", async () => {
    // Guarding the guard. `anonymous()` returning [] for the wrong reason — a selector that matches
    // nothing, a render that failed — is the failure mode that makes a green a11y test worthless.
    renderWithProviders(<Settings />);
    await screen.findByText(/Appearance|Aparência/i);

    const first = document.querySelector("input, select, textarea") as HTMLElement;
    expect(first, "found no controls at all").toBeTruthy();
    const held = first.getAttribute("aria-label");
    first.removeAttribute("aria-label");
    first.removeAttribute("aria-labelledby");
    first.removeAttribute("id");

    expect(anonymous().length).toBeGreaterThan(0);
    if (held) first.setAttribute("aria-label", held);
  });
});
