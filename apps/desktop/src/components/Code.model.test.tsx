import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getDoctor,
  getFsTree,
  getGitStatus,
  getModels,
  getPostureFacts,
  getRuns,
  patchConfig,
  streamCodeTurn,
} from "@/lib/api";
import { emptyTree, gitStatus, modelOption, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { renderWithProviders } from "@/test/utils";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** The doctor answer the picker's neighbour reads. One installed external agent, so the tests that
 *  need to switch worker have something to switch to. */
/** The Code screen, inside the one provider `renderWithProviders` does not carry.
 *
 *  `App` mounts the tooltip provider around everything; the provider picker's tooltips need it, and
 *  without it Radix throws the moment an external agent appears in the row. */
function renderCode() {
  return renderWithProviders(
    <TooltipProvider>
      <Code />
    </TooltipProvider>,
  );
}

function doctorWith(agents: { key: string; label: string }[] = []) {
  return {
    has_any_key: true,
    configured_providers: ["openrouter"],
    default_model: "test/model",
    tiers: { weak: "w", mid: "m", top: "t" },
    memory_backend: "sqlite",
    cache: true,
    sandbox: "local",
    // The editor's own capability probes. Empty here: this suite is about the model row, and an
    // empty list is what a machine with no local completion server reports.
    editor: [],
    external_agents: agents.map((a) => ({
      ...a,
      available: true,
      command: "npx -y agent",
      notes: "ready",
      install_hint: "",
      writes_directly: true,
    })),
  };
}

/**
 * Choosing the model from the composer.
 *
 * The gap this closes is narrow and was invisible: `/api/code/turn` has accepted a `model` field
 * since it existed, and no client ever sent one — so every conversation in the app ran on
 * `CHIMERA_DEFAULT_MODEL`, and the only way to change it was Settings, a text box and a slug typed
 * from memory. The assertions below are therefore mostly about the REQUEST, not the menu: a picker
 * that changes a chip and sends the same body as before is the bug wearing the fix's clothes.
 */
describe("Code — choosing the model", () => {
  beforeEach(() => {
    localStorage.setItem(WORKSPACE_KEY, "/repo");
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getDoctor).mockResolvedValue(doctorWith());
    vi.mocked(getModels).mockResolvedValue({
      default: "openrouter/openai/gpt-5.5",
      models: [
        modelOption({ slug: "openrouter/deepseek/deepseek-chat-v3.1", label: "DeepSeek: V3.1", recommended: true }),
        modelOption({ slug: "openrouter/z-ai/glm-4.6", label: "Z.ai: GLM 4.6", vendor: "Z.ai" }),
      ],
      sources: ["catalog", "openrouter"],
      reason: "",
    });
  });

  it("sends no model at all until one is picked", async () => {
    // The field must be ABSENT, not empty or null: absent is what makes the server fall back to the
    // configured default, which is what every build before this picker did. An empty string would
    // be a new value the server has to special-case, and a null would be a client claiming to have
    // made a choice it did not make.
    const user = userEvent.setup();
    renderCode();

    await user.type(await screen.findByPlaceholderText(/Ask about this code/i), "oi");
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).not.toHaveProperty("model");
  });

  it("sends the picked model with the next message", async () => {
    const user = userEvent.setup();
    renderCode();

    await user.click(await screen.findByRole("button", { name: /default/i }));
    await user.click(await screen.findByText("DeepSeek: V3.1"));

    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "oi");
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).toMatchObject({
      model: "openrouter/deepseek/deepseek-chat-v3.1",
    });
  });

  it("asks the catalogue only once somebody opens the menu", async () => {
    // A composer nobody has touched must not reach a public catalogue on the network. This is the
    // whole reason the query is `enabled` rather than eager.
    const user = userEvent.setup();
    renderCode();
    await screen.findByPlaceholderText(/Ask about this code/i);
    expect(getModels).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /default/i }));
    await waitFor(() => expect(getModels).toHaveBeenCalled());
  });

  it("keeps a way back to the default that typing cannot hide", async () => {
    const user = userEvent.setup();
    renderCode();

    await user.click(await screen.findByRole("button", { name: /default/i }));
    await user.click(await screen.findByText("Z.ai: GLM 4.6"));
    await user.click(screen.getByRole("button", { name: /glm-4.6/i }));
    // A search that matches nothing still leaves the row that undoes the choice.
    await user.type(await screen.findByPlaceholderText(/Search by name/i), "zzzzz");
    await user.click(screen.getByText("openrouter/openai/gpt-5.5"));

    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "oi");
    await user.click(screen.getByRole("button", { name: /^Send$/i }));
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).not.toHaveProperty("model");
  });

  it("says when a model cannot call tools, because the turn would only describe the edit", async () => {
    const user = userEvent.setup();
    vi.mocked(getModels).mockResolvedValue({
      default: "openrouter/openai/gpt-5.5",
      models: [modelOption({ slug: "openrouter/toy/no-tools", label: "Toy: No Tools", tools: false })],
      sources: ["openrouter"],
      reason: "",
    });
    renderCode();

    await user.click(await screen.findByRole("button", { name: /default/i }));
    await user.click(await screen.findByText("Toy: No Tools"));

    expect(await screen.findByText(/Cannot call tools/i)).toBeInTheDocument();
  });

  it("stays quiet about tools when the catalogue did not say", async () => {
    // `null` is "we were not told", and warning on it would train the user to ignore the warning in
    // the one case where it is real.
    const user = userEvent.setup();
    vi.mocked(getModels).mockResolvedValue({
      default: "openrouter/openai/gpt-5.5",
      models: [modelOption({ slug: "ollama/llama3", label: "llama3", tools: null, source: "ollama" })],
      sources: ["ollama"],
      reason: "",
    });
    renderCode();

    await user.click(await screen.findByRole("button", { name: /default/i }));
    await user.click(await screen.findByText("llama3"));

    expect(screen.queryByText(/Cannot call tools/i)).not.toBeInTheDocument();
  });

  it("shows the curated list next to the reason the full one is missing", async () => {
    // Not INSTEAD of it. An empty menu behind an error reads as "your key buys nothing", which is
    // not what a failed fetch means — the models still listed are real and callable.
    const user = userEvent.setup();
    vi.mocked(getModels).mockResolvedValue({
      default: "openrouter/openai/gpt-5.5",
      models: [modelOption({ label: "Curated: Model", recommended: true })],
      sources: ["catalog"],
      reason: "unreachable",
    });
    renderCode();

    await user.click(await screen.findByRole("button", { name: /default/i }));
    expect(await screen.findByText(/full catalogue did not answer/i)).toBeInTheDocument();
    expect(screen.getByText("Curated: Model")).toBeInTheDocument();
  });

  it("offers to make the pick the standing default, and only then writes it", async () => {
    const user = userEvent.setup();
    renderCode();

    await user.click(await screen.findByRole("button", { name: /default/i }));
    // Nothing picked yet: the button would write what is already written.
    expect(screen.getByRole("button", { name: /Make it the default/i })).toBeDisabled();

    await user.click(await screen.findByText("DeepSeek: V3.1"));
    expect(patchConfig).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: /deepseek-chat-v3.1/i }));
    await user.click(await screen.findByRole("button", { name: /Make it the default/i }));
    await waitFor(() =>
      expect(patchConfig).toHaveBeenCalledWith({
        CHIMERA_DEFAULT_MODEL: "openrouter/deepseek/deepseek-chat-v3.1",
      }),
    );
  });

  it("disappears when an external agent is doing the work", async () => {
    // Claude Code picks its own model. A selector next to it would offer a choice this app cannot
    // make, and the turn would run on something other than what the row says.
    const user = userEvent.setup();
    vi.mocked(getDoctor).mockResolvedValue(doctorWith([{ key: "claude", label: "Claude Code" }]));
    renderCode();

    expect(await screen.findByRole("button", { name: /default/i })).toBeInTheDocument();
    // Awaited, not read synchronously: the worker row appears when `doctor` resolves, and the model
    // chip is already on screen before that — so a `getBy` here races the fetch it depends on.
    await user.click(await screen.findByRole("button", { name: "Claude Code" }));
    expect(screen.queryByRole("button", { name: /default/i })).not.toBeInTheDocument();
  });

  it("does not send a model to an external agent even if one was picked first", async () => {
    // The order that produces the bug: pick a model, then hand the work to Claude Code. The field
    // has to drop out of the request, or the transcript claims a routing that did not happen.
    const user = userEvent.setup();
    vi.mocked(getDoctor).mockResolvedValue(doctorWith([{ key: "claude", label: "Claude Code" }]));
    renderCode();

    await user.click(await screen.findByRole("button", { name: /default/i }));
    await user.click(await screen.findByText("DeepSeek: V3.1"));
    await user.click(screen.getByRole("button", { name: "Claude Code" }));

    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "oi");
    await user.click(screen.getByRole("button", { name: /^Send$/i }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    const sent = vi.mocked(streamCodeTurn).mock.calls[0][0];
    expect(sent).not.toHaveProperty("model");
    expect(sent).toMatchObject({ provider: "claude" });
  });
});
