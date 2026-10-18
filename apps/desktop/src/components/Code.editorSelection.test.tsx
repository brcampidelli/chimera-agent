import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { queueConversationSeed } from "@/lib/appCommands";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const ITEM = { kind: "file" as const, path: "src/app.py", start_line: 4, end_line: 5 };

/**
 * "Ask Chimera" from the Editor arrives here: the question is sent in the conversation of the same
 * project, with the lines riding as one `context` item — the same item the composer's `@` sends,
 * read from disk and fenced as file content by the server, never pasted into the person's words —
 * and the exchange says which lines it was about.
 */
describe("Code — a question asked from the Editor", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockReset().mockImplementation(scriptTurn());
  });

  it("sends it once, in that project, with the lines as a context item beside the words", async () => {
    localStorage.setItem("chimera:code:workspace", "/repo");
    queueConversationSeed({ workspace: "/repo", message: "why 2?", context: { item: ITEM, label: "src/app.py:4–5" } });
    renderWithProviders(<Code />);

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    const req = vi.mocked(streamCodeTurn).mock.calls[0][0];
    expect(req.message).toBe("why 2?");
    expect(req.workspace).toBe("/repo");
    expect(req.context).toEqual([ITEM]);
    // A question, not a proposal: nothing here narrows the turn or asks for a replacement.
    expect("propose_change" in req).toBe(false);

    expect(await screen.findByTestId("exchange-selection")).toHaveTextContent("From the editor: src/app.py, lines 4–5");
    // Taken, not replayed: a re-render does not send it a second time.
    await waitFor(() => expect(screen.getAllByTestId("exchange-selection")).toHaveLength(1));
    expect(streamCodeTurn).toHaveBeenCalledTimes(1);
  });

  it("sends no context on a message typed in the conversation with nothing pointed at", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "hello{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect("context" in vi.mocked(streamCodeTurn).mock.calls[0][0]).toBe(false);
  });
});
