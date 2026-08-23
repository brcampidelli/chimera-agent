import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns } from "@/lib/api";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { emptyTree, gitStatus, postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const A_PROJECT = "/home/me/the-project";

/**
 * The plain conversation — ask it something, no project, no files — and the way into one.
 *
 * A turn with no workspace has always worked: it runs, answers, is priced, reads memory, and the
 * sidebar already groups such sessions under their own heading. What did not exist was any way to
 * START one. Once a folder was chosen, every new conversation inherited it and nothing on the
 * screen let go, so the project-less conversation was reachable only by never having opened a
 * project in the first place.
 *
 * That is why this is a control and not a second screen. The server also carries `/api/chat/stream`
 * and its own session store, and building a screen on THAT would have given the user two chats
 * whose histories never meet — the same question asked twice, answered from two memories, with
 * nothing saying which is which.
 */
describe("Code — the conversation with no project", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getRuns).mockResolvedValue([]);
    localStorage.setItem(WORKSPACE_KEY, A_PROJECT);
  });

  it("offers a way out once a project is open", async () => {
    renderWithProviders(<Code />);

    expect(await screen.findByRole("button", { name: /Leave the project/ })).toBeTruthy();
  });

  it("does not offer one when there is nothing to leave", async () => {
    localStorage.removeItem(WORKSPACE_KEY);
    renderWithProviders(<Code />);

    await screen.findByPlaceholderText(/folder path/i);
    expect(screen.queryByRole("button", { name: /Leave the project/ })).toBeNull();
  });

  it("actually lets go of the project", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    await user.click(await screen.findByRole("button", { name: /Leave the project/ }));

    // Cleared rather than stored as "": "no choice" round-trips as absence, which is what
    // `writeWorkspace` documents and what a next launch has to read.
    await waitFor(() => expect(localStorage.getItem(WORKSPACE_KEY)).toBeNull());
  });

  it("the button that leaves is not a button that submits", async () => {
    // `Escolher pasta` was a submit by omission and discarded the open project before showing a
    // single folder. This one sits in the same form and does the opposite thing on purpose, so it
    // is the one most likely to be given the same defect by a future edit.
    renderWithProviders(<Code />);

    const leave = await screen.findByRole("button", { name: /Leave the project/ });

    expect(leave.getAttribute("type")).toBe("button");
  });
});
