import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import {
  answerApproval,
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  streamCodeTurn,
  type CodeApprovalEvent,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A parked turn is a turn WAITING, and the screen has to be able to say so.
 *
 * The tool call that raised the question is blocked on a worker thread until someone answers or the
 * deadline passes, so the conversation is neither finished nor broken — and everything already on it
 * has to stay readable, because the answer to "may it write this file" is usually somewhere in what
 * the model just said. A question drawn over a blanked transcript would be asking a person to decide
 * with the evidence taken away.
 *
 * Measured shape of the alternative, from `chimera tui`: a `run_shell` blocked for 123.8 s against a
 * 120 s timeout and came back as `✗ run_shell` with no explanation, because the surface was governed
 * and its question could not be drawn.
 */
/** A question asked just now, which is what the live frame carries — the server emits it as the
 *  call parks. Written relative to the clock rather than as a fixed epoch on purpose: a hardcoded
 *  `asked_at` ages into a question that expired months ago, and the card would correctly refuse to
 *  draw buttons for it. That the first draft of this file did exactly that, and the card called it,
 *  is the reason the countdown reads `asked_at` at all. */
const ASKED: CodeApprovalEvent = {
  id: "q-7f3a",
  action: "write_file(path='notes.md')",
  reason: "write_file is restricted after this run consumed untrusted content",
  asked_at: Date.now() / 1000,
  wait_seconds: 300,
};

describe("Code — a turn parked on a question", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  /** Send one turn that streams a sentence and then parks. The stream stays open, as the server's
   *  does: the call is waiting for the answer this screen is supposed to collect. */
  async function parkedTurn() {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({
        tokens: ["I read the page you linked. ", "Writing the summary to notes.md."],
        approval: ASKED,
        parked: true,
      }),
    );
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "summarise that page");
    await user.click(screen.getByRole("button", { name: "Send" }));
    return user;
  }

  it("draws the question the turn is parked on, in the ledger's own words", async () => {
    await parkedTurn();

    await screen.findByRole("group", { name: /decision/i });
    expect(screen.getByText(ASKED.reason)).toBeInTheDocument();
    expect(screen.getByText(ASKED.action)).toBeInTheDocument();
  });

  it("keeps the turn readable while it waits — the answer is usually in what was just said", async () => {
    await parkedTurn();

    await screen.findByRole("group", { name: /decision/i });
    expect(screen.getByText(/Writing the summary to notes\.md\./)).toBeInTheDocument();
  });

  it("says what silence will do, with the deadline the frame carried", async () => {
    await parkedTurn();

    await screen.findByRole("group", { name: /decision/i });
    // A range, not `300s`. The card counts from `asked_at`, so the number on screen is the time
    // LEFT and the seconds this test itself took have already come off it. Pinning the exact value
    // would be asserting that the countdown does not work.
    const line = screen.getByText(/refuses after \d+s/i);
    const left = Number(/(\d+)s/.exec(line.textContent ?? "")?.[1]);
    expect(left).toBeGreaterThan(280);
    expect(left).toBeLessThanOrEqual(300);
  });

  it("allowing answers THAT question with true and takes the card down", async () => {
    const user = await parkedTurn();
    await screen.findByRole("group", { name: /decision/i });

    await user.click(screen.getByRole("button", { name: /allow this once/i }));

    await waitFor(() => expect(answerApproval).toHaveBeenCalledWith("q-7f3a", true));
    await waitFor(() =>
      expect(screen.queryByRole("group", { name: /decision/i })).not.toBeInTheDocument(),
    );
  });

  it("refusing answers with false — the person's no reaches the call, not the timeout", async () => {
    const user = await parkedTurn();
    await screen.findByRole("group", { name: /decision/i });

    await user.click(screen.getByRole("button", { name: /^refuse$/i }));

    await waitFor(() => expect(answerApproval).toHaveBeenCalledWith("q-7f3a", false));
  });

  it("draws nothing when a turn never parks", async () => {
    // The card is the exception, not the furniture: a turn that asks nothing must look exactly as
    // it did before questions existed.
    const user = userEvent.setup();
    // The answer, not the tokens: a finished turn replaces the streamed deltas with `done.answer`,
    // which is why the parked cases above assert on tokens and this one cannot.
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({ done: { answer: "Nothing to ask about." } }),
    );
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "hello");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await screen.findByText(/Nothing to ask about\./);
    expect(screen.queryByRole("group", { name: /decision/i })).not.toBeInTheDocument();
  });
});
