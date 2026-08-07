import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * There were two doors to the same agent. The chat and the coding turn ran the same base tools, and
 * only one of them was assembled with a write region, a denylist and a taint ledger — so the door
 * without the guard was also the more permissive one, and nothing on either screen said so.
 *
 * One conversation now. These are the things the surviving screen had to gain for that to be a merge
 * rather than a deletion.
 */
describe("Code — the one conversation", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
  });

  it("sends on Enter and breaks the line on Shift+Enter", async () => {
    // The chat's habit, kept for the merged screen. It is the riskier choice now that a turn edits
    // files — what makes it affordable is that the turn is verified and the edits are reversible.
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    const box = screen.getByPlaceholderText(/^Ask about this code/);

    await user.type(box, "just a line{Shift>}{Enter}{/Shift}");
    expect(streamCodeTurn).not.toHaveBeenCalled();

    await user.type(box, "{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledOnce());
  });

  it("can abandon a turn instead of waiting it out", async () => {
    // `streamCodeTurn` always accepted an AbortSignal and nothing ever passed one, so a turn that
    // went wrong had to be waited out with the composer disabled.
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(() => new Promise<void>(() => {}));
    renderWithProviders(<Code />);

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "long one{Enter}");
    const stop = await screen.findByRole("button", { name: "Stop" });
    await user.click(stop);

    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
  });

  it("offers fusion, and warns that it turns off the agent's ability to act", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.click(screen.getByRole("button", { name: /Fuse/i }));

    // The warning while the toggle is armed — the last one before Enter.
    await screen.findByText(/answers without tools/i);

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "which design is better?{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].fuse).toBe(true);
  });

  it("marks an answer that came from fusion, where the reader is looking", async () => {
    // Zero tool calls is the same number a turn that needed none reports. Without this mark the two
    // are indistinguishable, and one of them is a confident description of a file never opened.
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ done: { fused: true } }));
    renderWithProviders(<Code />);

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "explain this{Enter}");
    await screen.findByText(/no file was read and no command was run/i);
  });
});
