import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A chat window has nobody standing by to restart it.
 *
 * `context_budget` is off by default in the library, and that default is deliberate: compaction
 * discards messages and an API caller who never asked for it should not silently get it. For a
 * conversation the user has been building for an hour, the same default is the wrong one — without
 * a budget the message list only grows, an overflow is terminal (the failover table maps
 * CONTEXT_OVERFLOW to ABORT), and the thread ends on a provider error with no way to continue.
 *
 * The field existed on the request type and on the endpoint the whole time. Nothing sent it, so the
 * compaction machinery could not run from this screen at all.
 */
describe("Code — the conversation asks for a context budget", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
  });

  async function ask(text: string) {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), `${text}{Enter}`);
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    return user;
  }

  it("sends a budget the server can act on", async () => {
    await ask("what is this?");
    const sent = vi.mocked(streamCodeTurn).mock.calls[0][0].context_budget;
    // Not just "present": null and 0 both read as OFF server-side, so a field that arrives empty is
    // the bug wearing the fix's clothes.
    expect(sent).toBeGreaterThan(0);
    expect(sent).toBeLessThanOrEqual(1);
  });

  it("sends it on the second turn too, not only the first", async () => {
    // The agent is rebuilt per turn from this request. A budget sent once is a budget that applied
    // once — and the turn where it matters is the late one, never the first.
    const user = await ask("first");
    await waitFor(() => expect(screen.getByPlaceholderText(/^Ask about this code/)).toBeEnabled());
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "second{Enter}");
    await waitFor(() => expect(vi.mocked(streamCodeTurn).mock.calls.length).toBeGreaterThan(1));

    const budgets = vi.mocked(streamCodeTurn).mock.calls.map((c) => c[0].context_budget);
    expect(budgets.every((b) => typeof b === "number" && b > 0)).toBe(true);
  });
});
