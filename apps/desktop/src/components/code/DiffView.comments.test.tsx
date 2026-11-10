import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DiffView } from "@/components/code/DiffView";
import { LineCommentsBar } from "@/components/code/LineComments";
import { useT } from "@/lib/i18n";
import {
  buildLineCommentsMessage,
  clearLineComments,
  lineCommentLabels,
  setLineCommentsNotice,
  useSendLineCommentsRequest,
} from "@/lib/line-comments";
import { renderWithProviders } from "@/test/utils";

/** Two body lines on each side of one change: context "keep", removed "old", added "new", context "end". */
const patch = ["@@ -1,3 +1,3 @@", " keep", "-old", "+new", " end"].join("\n");
const ORIGIN = "edit:0:0";

/** What the conversation does with its comments: build the message, hand it on, clear only when it was
 *  accepted. The real Conversation does the same, and its own test covers it. A busy conversation keeps
 *  the comments and says why. */
function Harness({
  sessionId,
  busy = false,
  onSend = () => true,
}: {
  sessionId: string;
  busy?: boolean;
  onSend?: (message: string) => boolean;
}) {
  const t = useT();
  const send = () => {
    if (busy) {
      setLineCommentsNotice(sessionId, t("code.lineComments.busy"));
      return;
    }
    const message = buildLineCommentsMessage(sessionId, lineCommentLabels(t), {});
    if (message !== null && onSend(message)) clearLineComments(sessionId);
  };
  useSendLineCommentsRequest(sessionId, send);
  return (
    <>
      <DiffView patch={patch} commentable={{ sessionId, file: "src/a.ts", origin: ORIGIN }} />
      <LineCommentsBar sessionId={sessionId} disabled={busy} onSend={send} />
    </>
  );
}

/** The `+` of a line, by its kind: the old and the new side can share a number. */
function plusFor(line: number, kind: string): HTMLElement {
  return screen.getByRole("button", { name: `+ Comment line ${line} (${kind})` });
}

describe("comments on the lines of a diff", () => {
  it("writes a comment on a line, shows it there, and sends it as one message naming its diff", async () => {
    const user = userEvent.setup();
    const sent: string[] = [];
    renderWithProviders(<Harness sessionId="conv-flow" onSend={(m) => (sent.push(m), true)} />);

    await user.click(plusFor(2, "added line"));
    await user.keyboard("rename it");
    await user.keyboard("{Enter}");

    expect(screen.getByText("rename it")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Send comments (1)" }));

    expect(sent).toEqual([
      [
        "Review comments on the diff (1):",
        "",
        "1. src/a.ts:2 (added line; edit diff of turn 1)",
        "   Snippet: `new`",
        "   Comment: rename it",
      ].join("\n"),
    ]);
    expect(screen.queryByText("rename it")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send comments/ })).not.toBeInTheDocument();
  });

  it("keeps the comments when the conversation refuses the message", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness sessionId="conv-refused" onSend={() => false} />);

    await user.click(plusFor(1, "context line"));
    await user.keyboard("keep me");
    await user.keyboard("{Enter}");
    await user.click(screen.getByRole("button", { name: "Send comments (1)" }));

    expect(screen.getByText("keep me")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send comments (1)" })).toBeInTheDocument();
  });

  it("sends every pending comment at once with Ctrl+Enter, in file and line order", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn((_message: string) => true);
    renderWithProviders(<Harness sessionId="conv-keys" onSend={onSend} />);

    await user.click(plusFor(1, "context line"));
    await user.keyboard("first");
    await user.keyboard("{Enter}");
    await user.click(plusFor(2, "added line"));
    await user.keyboard("second");
    // Ctrl+Enter saves the box it is typed in, then sends the lot.
    await user.keyboard("{Control>}{Enter}{/Control}");

    expect(onSend).toHaveBeenCalledTimes(1);
    const message = onSend.mock.calls[0]?.[0] as string;
    expect(message.indexOf("src/a.ts:1")).toBeLessThan(message.indexOf("src/a.ts:2"));
    expect(message).toContain("Comment: first");
    expect(message).toContain("Comment: second");
    expect(screen.queryByRole("button", { name: /Send comments/ })).not.toBeInTheDocument();
  });

  it("says briefly why Ctrl+Enter did not send while a turn runs, and keeps the comment", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(() => true);
    renderWithProviders(<Harness sessionId="conv-busy" busy onSend={onSend} />);

    await user.click(plusFor(2, "added line"));
    await user.keyboard("wait");
    await user.keyboard("{Control>}{Enter}{/Control}");

    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByRole("status")).toHaveTextContent("Wait for the turn to finish");
    expect(screen.getByText("wait")).toBeInTheDocument();
  });

  it("returns focus to the line's + when the box is cancelled with Escape", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness sessionId="conv-esc" />);

    await user.click(plusFor(1, "context line"));
    await user.keyboard("never saved");
    await user.keyboard("{Escape}");

    expect(screen.queryByText("never saved")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Send comments/ })).not.toBeInTheDocument();
    expect(plusFor(1, "context line")).toHaveFocus();
  });

  it("returns focus to the line's + after a comment is saved", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness sessionId="conv-focus-save" />);

    await user.click(plusFor(2, "removed line"));
    await user.keyboard("saved");
    await user.keyboard("{Enter}");

    expect(plusFor(2, "removed line")).toHaveFocus();
  });

  it("edits a saved comment in place and removes it when it is emptied", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn(() => true);
    renderWithProviders(<Harness sessionId="conv-edit" onSend={onSend} />);

    await user.click(plusFor(1, "context line"));
    await user.keyboard("draft");
    await user.keyboard("{Enter}");

    await user.click(screen.getByRole("button", { name: "Edit comment on line 1 (context line)" }));
    const box = screen.getByRole("textbox", { name: "Comment on line 1 (context line)" });
    expect(box).toHaveValue("draft");
    await user.clear(box);
    await user.keyboard("final{Enter}");
    expect(screen.getByText("final")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit comment on line 1 (context line)" }));
    await user.clear(screen.getByRole("textbox", { name: "Comment on line 1 (context line)" }));
    await user.keyboard("{Enter}");
    expect(screen.queryByRole("button", { name: /Send comments/ })).not.toBeInTheDocument();
    expect(onSend).not.toHaveBeenCalled();
  });

  it("deletes a saved comment from its button and gives focus back to the line", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness sessionId="conv-remove" />);

    await user.click(plusFor(1, "context line"));
    await user.keyboard("gone{Enter}");
    await user.click(screen.getByRole("button", { name: "Delete comment on line 1 (context line)" }));

    expect(screen.queryByText("gone")).not.toBeInTheDocument();
    expect(plusFor(1, "context line")).toHaveFocus();
  });

  it("keeps a single + in the tab order and moves between the others with the arrow keys", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness sessionId="conv-roving" />);

    const plusButtons = screen.getAllByRole("button", { name: /^\+ Comment line/ });
    expect(plusButtons.filter((b) => b.tabIndex === 0)).toHaveLength(1);

    plusFor(1, "context line").focus();
    await user.keyboard("{ArrowDown}");
    expect(plusFor(2, "removed line")).toHaveFocus();
    expect(plusFor(2, "removed line").tabIndex).toBe(0);
    expect(plusFor(1, "context line").tabIndex).toBe(-1);
  });

  it("keeps the header lines of the diff on one line each", () => {
    renderWithProviders(<Harness sessionId="conv-header" />);
    expect(screen.getByText("@@ -1,3 +1,3 @@").closest("div")).toHaveClass("whitespace-pre");
  });

  it("leaves a diff without a conversation plain, with no + on its lines", () => {
    renderWithProviders(<DiffView patch={patch} />);
    expect(screen.queryByRole("button", { name: /Comment line/ })).not.toBeInTheDocument();
    expect(screen.getByText("+new")).toBeInTheDocument();
  });
});
