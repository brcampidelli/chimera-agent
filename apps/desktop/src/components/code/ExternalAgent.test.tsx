import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PostureNote } from "@/components/code/PostureNote";
import { ProviderPicker } from "@/components/code/ProviderPicker";
import { getDoctor, getPostureFacts } from "@/lib/api";
import { postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";
import { TooltipProvider } from "@/components/ui/tooltip";

vi.mock("@/lib/api", () => ({ getDoctor: vi.fn(), getPostureFacts: vi.fn() }));

/**
 * Handing the work to somebody else's agent, on screen.
 *
 * Two things are being held to account here, and only one of them is a feature. The feature is the
 * picker. The other is the sentence: the moment an external agent does the work, "edits inside
 * /repo" stops being a boundary Chimera enforces and becomes a description of the calls it happened
 * to see. A screen that kept the old sentence would be making a promise the turn cannot keep, which
 * is the one failure this product cannot absorb.
 */

function doctor(agents: unknown[]) {
  return {
    has_any_key: true,
    configured_providers: ["openrouter"],
    default_model: "m",
    tiers: { weak: "w", mid: "m", top: "t" },
    memory_backend: "sqlite",
    cache: true,
    sandbox: "local",
    external_agents: agents,
  };
}

const CLAUDE = {
  key: "claude",
  label: "Claude Code",
  available: true,
  command: "npx -y @agentclientprotocol/claude-agent-acp",
  install_hint: "needs Node 22+",
  writes_directly: true,
  notes: "Has its own file tools.",
};

function picker(props: Partial<Parameters<typeof ProviderPicker>[0]> = {}) {
  return renderWithProviders(
    <TooltipProvider>
      <ProviderPicker value="" onChange={() => {}} {...props} />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  vi.mocked(getDoctor).mockResolvedValue(doctor([CLAUDE]) as never);
  vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
});

describe("ProviderPicker", () => {
  it("offers the agents this machine actually has", async () => {
    picker();
    expect(await screen.findByRole("button", { name: "Claude Code" })).toBeEnabled();
    // And Chimera's own loop, which is what "" means and what the screen starts on.
    expect(screen.getByRole("button", { name: "Chimera" })).toBeInTheDocument();
  });

  it("shows an agent it cannot run, disabled, beside one it can", async () => {
    // "Gemini needs npm i -g" is useful information standing next to a Claude Code you can press.
    vi.mocked(getDoctor).mockResolvedValue(
      doctor([CLAUDE, { ...CLAUDE, key: "gemini", label: "Gemini CLI", available: false }]) as never,
    );
    picker();
    expect(await screen.findByRole("button", { name: "Claude Code" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Gemini CLI" })).toBeDisabled();
  });

  it("disappears entirely when nothing here can run", async () => {
    // The composer is the most valuable strip in the app; a permanently greyed control there is
    // clutter for a capability the user cannot reach. `chimera doctor` is where "you do not have
    // this yet, here is how" belongs — and it reports every agent whether or not this row does.
    vi.mocked(getDoctor).mockResolvedValue(
      doctor([{ ...CLAUDE, available: false }]) as never,
    );
    const { container } = picker();
    await waitFor(() => expect(getDoctor).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("says nothing at all when the backend reports no catalogue", async () => {
    vi.mocked(getDoctor).mockResolvedValue(doctor([]) as never);
    const { container } = picker();
    await waitFor(() => expect(getDoctor).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("reports the choice by key", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    picker({ onChange });

    await user.click(await screen.findByRole("button", { name: "Claude Code" }));
    expect(onChange).toHaveBeenCalledWith("claude");
  });
});

describe("the sentence, when somebody else does the work", () => {
  it("promises the snapshot instead of the boundary", async () => {
    // The native sentence names limits ("edits inside /repo, runs no shell"). Those limits belong to
    // tools Chimera owns. An external agent brings its own, so the honest promise is the smaller
    // one: we took a copy first and you can undo the whole turn.
    vi.mocked(getPostureFacts).mockResolvedValue(
      postureFacts({ external_agent: "claude", workspace: "/repo" }),
    );
    renderWithProviders(
      <PostureNote workspace="/repo" reach="workspace" approval="suspicious" provider="claude" />,
    );

    expect(await screen.findByText(/undo the whole turn/i)).toBeInTheDocument();
    expect(screen.queryByText(/runs no shell/i)).toBeNull();
  });

  it("says out loud that the limits are not enforced here", async () => {
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts({ external_agent: "claude" }));
    renderWithProviders(
      <PostureNote workspace="/repo" reach="workspace" approval="suspicious" provider="claude" />,
    );
    expect(
      await screen.findByText(/without asking Chimera|change files without asking/i),
    ).toBeInTheDocument();
  });

  it("keeps the native sentence when Chimera does the work", async () => {
    // The guard against fixing this by making every sentence vague.
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    renderWithProviders(<PostureNote workspace="/repo" reach="workspace" approval="suspicious" />);

    expect(await screen.findByText(/\/repo/)).toBeInTheDocument();
    expect(screen.queryByText(/undo the whole turn/i)).toBeNull();
  });

  it("asks the server about the provider rather than deciding locally", async () => {
    // The facts come from the machine, always. A frontend that decided "external means unsafe" on
    // its own would be a second copy of a rule that already lives server-side.
    renderWithProviders(
      <PostureNote workspace="/repo" reach="workspace" approval="suspicious" provider="claude" />,
    );
    await waitFor(() =>
      expect(getPostureFacts).toHaveBeenCalledWith(
        "workspace",
        "suspicious",
        "/repo",
        "turn",
        "claude",
      ),
    );
  });
});
