import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";
import { WORKSPACE_KEY } from "@/lib/workspace";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** The screen was usable and simply did not remember.
 *
 * With no stored choice the backend falls back to its own launch directory. In a terminal that is
 * where you were standing, so it is right by accident; for a packaged app it is wherever the
 * shortcut points, which on Windows is a directory the user cannot even write to. Picking the right
 * folder and losing it on the next launch is what made that default feel like a trap.
 */
describe("Code — the chosen workspace persists", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  it("sends no workspace when the user has not chosen one", async () => {
    // Deliberately NOT a guessed default: inventing one would silently widen where the agent may
    // write, and the backend's own fallback is at least visible in the posture sentence.
    renderWithProviders(<Code />);
    await waitFor(() => expect(getPostureFacts).toHaveBeenCalled());
    expect(vi.mocked(getPostureFacts).mock.calls[0][2]).toBeNull();
  });

  it("remembers the root across a remount, which is what a restart is", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<Code />);

    const field = await screen.findByPlaceholderText(/folder path/i);
    await user.clear(field);
    await user.type(field, "/home/me/project");
    await user.click(screen.getByRole("button", { name: /open|abrir/i }));

    await waitFor(() => expect(localStorage.getItem(WORKSPACE_KEY)).toBe("/home/me/project"));

    unmount();
    vi.mocked(getPostureFacts).mockClear();
    renderWithProviders(<Code />);

    await waitFor(() => expect(getPostureFacts).toHaveBeenCalled());
    expect(vi.mocked(getPostureFacts).mock.calls[0][2]).toBe("/home/me/project");
  });

  it("a stored root reaches what the agent is ALLOWED to touch, not just what is displayed", async () => {
    // Asserted through the posture query because that is the one that answers "where may it write".
    // A screen that displays the right project while the agent edits somewhere else would be worse
    // than the original bug: it would look correct.
    localStorage.setItem(WORKSPACE_KEY, "/home/me/project");
    renderWithProviders(<Code />);

    await waitFor(() => expect(getPostureFacts).toHaveBeenCalled());
    expect(vi.mocked(getPostureFacts).mock.calls[0][2]).toBe("/home/me/project");
  });
});
