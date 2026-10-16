import { screen, waitFor, within } from "@testing-library/react";
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
  streamCodeTurn,
} from "@/lib/api";
import { emptyTree, gitStatus, modelOption, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { renderWithProviders } from "@/test/utils";
import { withComposerPickersOpen } from "@/test/composer";
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
    local_model: false,
    can_answer: true,
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
 * The reasoning variant beside the model, and the pin that keeps both with the conversation (P2-15).
 *
 * What is asserted is the REQUEST: a picked model travels with `model_pinned`, so the server keeps it
 * for the conversation's later turns; a variant is the same slug with `#high`; and the effort row
 * appears only for a model the provider says reasons, so it never offers a parameter that would be
 * dropped on the floor.
 */
describe("Code — the reasoning variant and the pinned model", () => {
  beforeEach(() => {
    localStorage.setItem(WORKSPACE_KEY, "/repo");
    withComposerPickersOpen();
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getDoctor).mockResolvedValue(doctorWith());
    vi.mocked(getModels).mockResolvedValue({
      default: "openrouter/openai/gpt-5.5",
      models: [
        modelOption({ slug: "openrouter/deepseek/deepseek-r1", label: "DeepSeek: R1", reasoning: true }),
        modelOption({ slug: "openrouter/z-ai/glm-4.6", label: "Z.ai: GLM 4.6", reasoning: false }),
      ],
      sources: ["openrouter"],
      reason: "",
    });
  });

  async function send(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByPlaceholderText(/Ask about this code/i), "oi");
    await user.click(screen.getByRole("button", { name: /^Send$/i }));
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    return vi.mocked(streamCodeTurn).mock.calls[0][0];
  }

  it("offers the effort for a reasoning model and sends the variant as a pinned pick", async () => {
    const user = userEvent.setup();
    renderCode();

    await user.click(await screen.findByRole("button", { name: /^default/i }));
    await user.click(await screen.findByText("DeepSeek: R1"));
    const effort = await screen.findByRole("group", { name: /^Effort$/i });
    await user.click(within(effort).getByRole("button", { name: /^High$/i }));

    expect(await send(user)).toMatchObject({
      model: "openrouter/deepseek/deepseek-r1#high",
      model_pinned: true,
    });
  });

  it("offers no effort for a model the provider does not say reasons", async () => {
    const user = userEvent.setup();
    renderCode();

    await user.click(await screen.findByRole("button", { name: /^default/i }));
    await user.click(await screen.findByText("Z.ai: GLM 4.6"));

    expect(screen.queryByRole("group", { name: /^Effort$/i })).toBeNull();
    expect(await send(user)).toMatchObject({ model: "openrouter/z-ai/glm-4.6", model_pinned: true });
  });

  it("sends no pin while nobody has picked, so the server keeps the conversation's own", async () => {
    const user = userEvent.setup();
    renderCode();
    await screen.findByPlaceholderText(/Ask about this code/i);

    const sent = await send(user);
    expect(sent).not.toHaveProperty("model");
    expect(sent).not.toHaveProperty("model_pinned");
  });

  it("going back to the default is a pin with no model, which unpins", async () => {
    const user = userEvent.setup();
    renderCode();

    await user.click(await screen.findByRole("button", { name: /^default/i }));
    await user.click(await screen.findByText("Z.ai: GLM 4.6"));
    await user.click(screen.getByRole("button", { name: /glm-4.6/i }));
    await user.click(await screen.findByText("openrouter/openai/gpt-5.5"));

    const sent = await send(user);
    expect(sent).not.toHaveProperty("model");
    expect(sent).toMatchObject({ model_pinned: true });
  });
});
