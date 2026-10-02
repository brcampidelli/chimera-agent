import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getCodeSession,
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  streamCodeTurn,
} from "@/lib/api";
import { writeWorkspace } from "@/lib/workspace";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The Code screen comes back to the conversation you were in.
 *
 * Found reading the code on 2026-09-30 (R16 of the review of several conversations at once). The
 * screen held the open conversation in component state that started at `null`, so leaving it — for
 * Settings, for the Work screen — and coming back landed on a blank new conversation, and switching
 * project and back did the same. The conversation was still in the list; the screen had forgotten
 * which one it was showing.
 *
 * Now the last conversation of each project is remembered, read when the screen opens and when the
 * project changes. It is never pushed into the conversation that is on screen: that one reloads the
 * stored transcript when its `resumeSession` changes, and doing so mid-turn would draw over the
 * turn it is streaming.
 */
describe("Code — coming back", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ session: "s1" }));
  });

  async function send(user: ReturnType<typeof userEvent.setup>, message: string) {
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), message);
    await user.click(screen.getByRole("button", { name: "Send" }));
  }

  async function openProject(user: ReturnType<typeof userEvent.setup>, path: string) {
    const field = screen.getByPlaceholderText(/folder path/i);
    await user.clear(field);
    await user.type(field, path);
    await user.click(screen.getByRole("button", { name: "Open" }));
  }

  it("reopens the conversation you left when the screen opens again", async () => {
    const user = userEvent.setup({ delay: null });
    const first = renderWithProviders(<Code />);
    await send(user, "what is here?");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledOnce());
    first.unmount();
    vi.mocked(getCodeSession).mockClear();

    renderWithProviders(<Code />);

    await waitFor(() => expect(getCodeSession).toHaveBeenCalledWith("s1"));
    await send(user, "and now?");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamCodeTurn).mock.calls[1][0].session_id).toBe("s1");
  });

  it("each project comes back to its own conversation", async () => {
    writeWorkspace("/a");
    const user = userEvent.setup({ delay: null });
    renderWithProviders(<Code />);
    await send(user, "in a");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledOnce());

    await openProject(user, "/b");
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ session: "s2" }));
    await send(user, "in b");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamCodeTurn).mock.calls[1][0].session_id).toBeNull();

    await openProject(user, "/a");
    await send(user, "back in a");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(3));
    expect(vi.mocked(streamCodeTurn).mock.calls[2][0]).toMatchObject({ session_id: "s1", workspace: "/a" });
  });

  it("a new conversation is what comes back after New conversation", async () => {
    const user = userEvent.setup({ delay: null });
    const first = renderWithProviders(<Code />);
    await send(user, "what is here?");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledOnce());
    await user.click(screen.getByRole("button", { name: "New conversation" }));
    first.unmount();
    vi.mocked(getCodeSession).mockClear();

    renderWithProviders(<Code />);
    await send(user, "fresh start");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamCodeTurn).mock.calls[1][0].session_id).toBeNull();
    expect(getCodeSession).not.toHaveBeenCalledWith("s1");
  });
});
