import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  streamCodeTurn,
  type CodeTurnHandlers,
  type CodeTurnInput,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * Three places where this screen already had the answer and did not say it.
 *
 * Not features in the usual sense — nothing new is fetched, no route is added. In each case the
 * value arrives, reaches the component, and is dropped: by a callback signature with no parameter,
 * by a receipt that draws nine badges and not the tenth, and by an observation parked in a native
 * `title=` where it cannot be selected or copied.
 */
describe("Code — the turn says why", () => {
  function turn(over: Partial<Record<string, unknown>> = {}, fail?: string) {
    return (_req: CodeTurnInput, h: CodeTurnHandlers) => {
      h.onSession?.("s1");
      if (fail !== undefined) {
        h.onError?.(fail);
        return Promise.resolve();
      }
      h.onTool?.({
        name: "run_shell",
        arguments: { cmd: "pytest" },
        ok: false,
        observation: "E   assert 1 == 2\nFAILED tests/test_x.py",
      });
      h.onDone?.({
        answer: "done",
        steps: 8,
        stopped_reason: "max_steps",
        tool_names: [],
        model: "m",
        prompt_tokens: 1,
        completion_tokens: 1,
        usd: 0,
        context_peak_tokens: 1,
        route_meta: null,
        ...over,
      } as never);
      return Promise.resolve();
    };
  }

  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  async function ask(impl: ReturnType<typeof turn>) {
    vi.mocked(streamCodeTurn).mockImplementation(impl as never);
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "go{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    return user;
  }

  it("shows what the server actually said when a turn fails", async () => {
    // "That turn failed." is the same sentence for a wrong API key, a rate limit, a model that does
    // not exist and a provider outage — four problems with four different fixes. The message was
    // arriving and being discarded by the callback's own signature, while three other screens in
    // this same app already displayed it.
    const user = await ask(turn({}, "AuthenticationError: invalid_api_key"));

    await user.click(await screen.findByText(/what the server said/i));
    expect(await screen.findByText(/invalid_api_key/)).toBeInTheDocument();
  });

  it("says the turn stopped at the step limit instead of looking finished", async () => {
    // `stopped_reason` arrives on every done frame and nothing outside types and mocks read it, so
    // a turn that stopped mid-task was pixel-for-pixel identical to one that finished the work.
    await ask(turn());
    expect(await screen.findByText(/stopped at the step limit/i)).toBeInTheDocument();
  });

  it("says nothing extra when the turn actually finished", async () => {
    // The other half. A badge on every turn would bury the four that mean "incomplete" among the
    // nine that are routine.
    await ask(turn({ stopped_reason: "final" }));
    await screen.findByText(/8 steps/i);
    expect(screen.queryByText(/stopped at/i)).not.toBeInTheDocument();
  });

  it("puts the tool output where it can be read and copied", async () => {
    // It lived in a native `title=`: not selectable, not copyable, and truncated again by the OS at
    // a length we do not control. A failing command's output is the thing people most want to paste
    // somewhere, and it was the one thing they could not take.
    await ask(turn());
    const output = await screen.findByText(/FAILED tests\/test_x\.py/);
    expect(output.tagName).toBe("PRE");
    expect(screen.getByText(/^copy$/i)).toBeInTheDocument();
  });

  it("says the output is clipped rather than promising the whole thing", async () => {
    // The server keeps the head and the tail of 400 characters and there is no route to the rest.
    // A label reading "full output" over the same 400 characters would be worse than the tooltip.
    await ask(turn());
    expect(await screen.findByText(/output \(clipped\)/i)).toBeInTheDocument();
  });
});
