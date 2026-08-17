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
 * A turn can take minutes. The next thing you want to say arrives long before it ends.
 *
 * The composer was `disabled` while busy, so a follow-up that occurred mid-turn had nowhere to go:
 * it was lost, or typed into some other window and pasted back. Queueing it is the fix, and the
 * part that needs care is what happens when the turn it was waiting behind FAILS — these turns edit
 * files, and a queued message firing into a failed verification would run against a tree the user
 * has not looked at and may be about to revert.
 */
describe("Code — a follow-up typed while the agent is working", () => {
  /** A turn that hangs until the test releases it, so "while busy" is a real state. */
  function heldTurn() {
    const sent: CodeTurnInput[] = [];
    let finish: ((opts?: { verifyFailed?: boolean }) => void) | null = null;
    const impl = (req: CodeTurnInput, h: CodeTurnHandlers) => {
      sent.push(req);
      h.onSession?.("s1");
      finish = (opts) => {
        if (opts?.verifyFailed) {
          h.onVerified?.({
            state: "failed",
            command: "pytest",
            source: "config",
            output: "1 failed",
            revert_token: "r1",
          } as never);
        }
        h.onDone?.({
          answer: "ok",
          steps: 1,
          stopped_reason: "final",
          tool_names: [],
          model: "m",
          prompt_tokens: 1,
          completion_tokens: 1,
          usd: 0,
          context_peak_tokens: 1,
          route_meta: null,
        } as never);
      };
      return Promise.resolve();
    };
    return { sent, impl, release: (o?: { verifyFailed?: boolean }) => finish?.(o) };
  }

  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  async function startTurn(turn: ReturnType<typeof heldTurn>) {
    vi.mocked(streamCodeTurn).mockImplementation(turn.impl as never);
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    const box = await screen.findByPlaceholderText(/^Ask about this code/);
    await user.type(box, "first thing{Enter}");
    await waitFor(() => expect(turn.sent.length).toBe(1));
    return { user, box };
  }

  it("takes the message instead of blocking the box, and sends it when the turn ends", async () => {
    const turn = heldTurn();
    const { user, box } = await startTurn(turn);

    // The box is live mid-turn — this is the whole complaint.
    expect(box).toBeEnabled();
    await user.type(box, "and also rename it{Enter}");

    // Held, and SAID to be held. An invisible queue is indistinguishable from a dropped message.
    expect(await screen.findByText(/and also rename it/)).toBeInTheDocument();
    expect(turn.sent.length).toBe(1);

    turn.release();
    await waitFor(() => expect(turn.sent.length).toBe(2));
    expect(turn.sent[1].message).toBe("and also rename it");
  });

  it("hands the message back instead of firing it at a failed turn", async () => {
    // These turns edit files. On a failed verification the screen offers Undo and Fix; a queued
    // message going out then races the user's decision and runs against a tree they have not read.
    const turn = heldTurn();
    const { user, box } = await startTurn(turn);
    await user.type(box, "and also rename it{Enter}");
    await waitFor(() => expect(screen.getByText(/and also rename it/)).toBeInTheDocument());

    turn.release({ verifyFailed: true });

    // Not sent — and not destroyed either: it is back in the box for the user to decide on.
    await waitFor(() => expect(box).toHaveValue("and also rename it"));
    expect(turn.sent.length).toBe(1);
  });

  it("gives the text back when the queue is cancelled", async () => {
    const turn = heldTurn();
    const { user, box } = await startTurn(turn);
    await user.type(box, "never mind this{Enter}");
    await screen.findByText(/never mind this/);

    await user.click(screen.getByRole("button", { name: /put back/i }));

    expect(box).toHaveValue("never mind this");
    turn.release();
    await new Promise((r) => setTimeout(r, 20));
    expect(turn.sent.length).toBe(1); // cancelled means cancelled, not deferred
  });
});
