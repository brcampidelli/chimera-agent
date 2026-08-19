import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Onboarding } from "@/components/Onboarding";
import { getConfig, getModels, patchConfig, testProviderKey } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getConfig: vi.fn(),
  // The wizard's model field can now be filled from the live catalogue instead of typed. Resolved
  // rather than bare so a suite about provider selection never waits on a list it does not use.
  getModels: vi.fn(async () => ({ default: "", models: [], sources: [], reason: "" })),
  patchConfig: vi.fn(async () => ({ updated: [] })),
  testProviderKey: vi.fn(async () => ({ ok: true, model: "x", error: null })),
}));

/** `/api/config` returns every credential slot, and half of them are tools rather than model
 *  providers. The fixture keeps that shape on purpose — `llm` is what the wizard filters on, and the
 *  suggested model and sign-up page now come from the backend rather than a copy kept here. */
function providers() {
  const llm = [
    ["OPENROUTER_API_KEY", "OpenRouter", "openrouter/openai/gpt-5.5", "https://openrouter.ai/keys"],
    ["OPENAI_API_KEY", "OpenAI", "openai/gpt-5.5", "https://platform.openai.com/api-keys"],
    [
      "ANTHROPIC_API_KEY",
      "Anthropic",
      "anthropic/claude-opus-4-8",
      "https://console.anthropic.com/settings/keys",
    ],
    ["GEMINI_API_KEY", "Gemini", "gemini/gemini-2.5-flash", "https://aistudio.google.com/apikey"],
    ["DEEPSEEK_API_KEY", "DeepSeek", "deepseek/deepseek-chat", "https://platform.deepseek.com/api_keys"],
  ].map(([env, label, model, keys_url]) => ({
    env,
    // The provider's routing name, which the model list is scoped by — derived server-side so the
    // client never re-implements "env var minus the suffix".
    name: env.replace("_API_KEY", "").toLowerCase(),
    label,
    set: false,
    hint: "",
    llm: true,
    model,
    keys_url,
  }));
  const tools = [
    ["TAVILY_API_KEY", "Tavily (web search)"],
    ["ELEVENLABS_API_KEY", "ElevenLabs (TTS)"],
    ["STABILITY_API_KEY", "Stability (images)"],
  ].map(([env, label]) => ({
    env,
    name: env.replace("_API_KEY", "").toLowerCase(),
    label,
    set: false,
    hint: "",
    llm: false,
    model: "",
    keys_url: "",
  }));
  return { providers: [...llm, ...tools] };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getConfig).mockResolvedValue(providers() as never);
});

/** The provider select. The cost-mode select is the other combobox on this screen, so the query has
 *  to be positional — asking for "the combobox" matches both. */
const combo = () => screen.getAllByRole("combobox")[0];
const keyField = () => screen.getByLabelText(/API key/);

describe("the first-run wizard", () => {
  it("offers the providers that serve a model, and none of the tool credentials", async () => {
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "Anthropic" })).toBeTruthy());

    const labels = within(combo())
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(labels).toEqual(["OpenRouter", "OpenAI", "Anthropic", "Gemini", "DeepSeek"]);
    // Saving one of these would leave `has_any_key` false, so the doctor would never flip and this
    // wizard would stay on screen forever with a key already stored.
    expect(labels).not.toContain("Tavily (web search)");
    expect(labels).not.toContain("ElevenLabs (TTS)");
  });

  it("writes the chosen provider's env var, not OpenRouter's", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "Anthropic" })).toBeTruthy());

    await user.selectOptions(combo(), "ANTHROPIC_API_KEY");
    await user.type(keyField(), "sk-ant-secret");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalled());
    const sent = vi.mocked(patchConfig).mock.calls[0][0];
    expect(sent.ANTHROPIC_API_KEY).toBe("sk-ant-secret");
    expect(sent.OPENROUTER_API_KEY).toBeUndefined();
  });

  it("takes the suggested model from the backend, not from a copy in the client", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "DeepSeek" })).toBeTruthy());

    await user.selectOptions(combo(), "DEEPSEEK_API_KEY");
    // Without the pin, the cost presets stay on OpenRouter slugs, `resolve_tiers` hands back a
    // ladder the user has no key for, and Test blames the brand-new key for OpenRouter's 401.
    expect(screen.getByDisplayValue("deepseek/deepseek-chat")).toBeTruthy();

    await user.type(keyField(), "sk-deep");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalled());
    expect(vi.mocked(patchConfig).mock.calls[0][0].CHIMERA_DEFAULT_MODEL).toBe(
      "deepseek/deepseek-chat",
    );
  });

  it("leaves the built-in default alone when OpenRouter's suggestion is untouched", async () => {
    // Writing it would freeze THIS build's default into the user's .env, so they would stop
    // inheriting the next one. The field still shows the value, because it is what will be used.
    const user = userEvent.setup();
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "OpenRouter" })).toBeTruthy());

    await user.type(keyField(), "sk-or-abc");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalled());
    const sent = vi.mocked(patchConfig).mock.calls[0][0];
    expect(sent.OPENROUTER_API_KEY).toBe("sk-or-abc");
    expect(sent.CHIMERA_DEFAULT_MODEL).toBeUndefined();
  });

  it("fills the model field from the live list, scoped to the key being pasted", async () => {
    // The wizard asks a question the rest of the app does not: what does THIS key buy, before it is
    // saved. Filtering by configured providers here would answer about whatever is already set up.
    const user = userEvent.setup();
    vi.mocked(getModels).mockResolvedValue({
      default: "",
      models: [
        {
          slug: "anthropic/claude-opus-5",
          label: "Anthropic: Claude Opus 5",
          vendor: "Anthropic",
          source: "openrouter",
          context_k: 200,
          input_per_m: 5,
          output_per_m: 25,
          tools: true,
          vision: true,
          free: false,
          recommended: false,
        },
      ],
      sources: ["openrouter"],
      reason: "",
    });
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "Anthropic" })).toBeTruthy());
    await user.selectOptions(combo(), "ANTHROPIC_API_KEY");

    await user.click(screen.getByRole("button", { name: /Browse/i }));
    await waitFor(() => expect(getModels).toHaveBeenCalledWith("anthropic"));
    await user.click(await screen.findByText("Anthropic: Claude Opus 5"));

    expect(screen.getByDisplayValue("anthropic/claude-opus-5")).toBeTruthy();

    await user.type(keyField(), "sk-ant");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalled());
    expect(vi.mocked(patchConfig).mock.calls[0][0].CHIMERA_DEFAULT_MODEL).toBe(
      "anthropic/claude-opus-5",
    );
  });

  it("keeps a model the user typed when they switch provider afterwards", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "OpenAI" })).toBeTruthy());

    const model = screen.getByDisplayValue("openrouter/openai/gpt-5.5");
    await user.clear(model);
    await user.type(model, "openrouter/mine");
    await user.selectOptions(combo(), "OPENAI_API_KEY");

    expect(screen.getByDisplayValue("openrouter/mine")).toBeTruthy();
    await user.type(keyField(), "sk-openai");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(patchConfig).toHaveBeenCalled());
    expect(vi.mocked(patchConfig).mock.calls[0][0].CHIMERA_DEFAULT_MODEL).toBe("openrouter/mine");
  });

  it("tests the model the user is about to depend on, not the built-in default", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "Gemini" })).toBeTruthy());

    await user.selectOptions(combo(), "GEMINI_API_KEY");
    await user.type(keyField(), "AIza-key");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(patchConfig).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Test key" }));

    await waitFor(() => expect(testProviderKey).toHaveBeenCalledWith("gemini/gemini-2.5-flash"));
  });

  it("sends the user to the chosen provider's own key page", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "Anthropic" })).toBeTruthy());

    await user.selectOptions(combo(), "ANTHROPIC_API_KEY");
    const link = screen.getByRole("link", { name: /Anthropic/ });
    expect(link.getAttribute("href")).toBe("https://console.anthropic.com/settings/keys");
  });

  it("offers no sign-up link for a provider it merely discovered", async () => {
    // A key for one of LiteLLM's other hundred vendors arrives with no page we can vouch for, and a
    // link to nowhere is worse than no link.
    const cfg = providers();
    cfg.providers.push({
      env: "GROQ_API_KEY",
      // Discovered providers carry a routing name too — that is the whole point of deriving it from
      // the env var rather than from a table of the five we ship.
      name: "groq",
      label: "Groq",
      set: true,
      hint: "…abcd",
      llm: true,
      model: "",
      keys_url: "",
    });
    vi.mocked(getConfig).mockResolvedValue(cfg as never);

    const user = userEvent.setup();
    renderWithProviders(<Onboarding onSkip={() => {}} />);
    await waitFor(() => expect(screen.getByRole("option", { name: "Groq" })).toBeTruthy());

    await user.selectOptions(combo(), "GROQ_API_KEY");
    expect(screen.queryByRole("link")).toBeNull();
  });
});
