import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getCodeSession, getFsTree, getGitStatus, getPostureFacts, getRuns, listCodeSessions } from "@/lib/api";
import { requestAppCommand } from "@/lib/appCommands";
import { forgetClosed } from "@/lib/closedConversations";
import { FIND_CURRENT, FIND_HIGHLIGHT } from "@/lib/findInConversation";
import { writeLastSession, writeWorkspace } from "@/lib/workspace";
import { emptyTree, gitStatus, postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function row(id: string, updated: number) {
  return { id, title: `conversation ${id}`, workspace: "/w", turns: 1, updated_at: updated };
}

/** The keydown on whatever has focus — the body, unless a test put it in a field. */
function key(init: KeyboardEventInit) {
  fireEvent.keyDown(document.activeElement ?? document.body, init);
}
const ctrlTab = { key: "Tab", code: "Tab", ctrlKey: true };
const ctrlShiftTab = { key: "Tab", code: "Tab", ctrlKey: true, shiftKey: true };
const reopen = { key: "T", code: "KeyT", ctrlKey: true, shiftKey: true };
const find = { key: "f", code: "KeyF", ctrlKey: true };

/** The find bar's field, told apart from the sidebar's conversation search by its name. */
const FIND_LABEL = "Find in conversation";

const opened = () => vi.mocked(getCodeSession).mock.calls.map(([id]) => id);
const last = () => opened()[opened().length - 1];

/**
 * The conversation screen's shortcuts (C4): Ctrl+Tab / Ctrl+⇧Tab walk the sidebar, ⌘⇧T reopens
 * what was left, ⌘F finds in the transcript. Driven through the real screen, because each lands
 * where a click on the sidebar row lands — through the same `resumeConversation`.
 */
describe("Code — conversation shortcuts", () => {
  beforeEach(() => {
    forgetClosed();
    writeWorkspace("/w");
    writeLastSession("/w", "s1");
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(listCodeSessions).mockResolvedValue([row("s1", 3), row("s2", 2), row("s3", 1)] as never);
    vi.mocked(getCodeSession).mockReset().mockImplementation(async (id: string) => ({
      id,
      workspace: "/w",
      exchanges: [{ you: `question for ${id}`, answer: `alpha beta ALPHA from ${id}`, tools: [], edits: [], done: null, verified: null }],
    }) as never);
  });
  afterEach(() => vi.unstubAllGlobals());

  async function ready() {
    renderWithProviders(<Code />);
    await screen.findByRole("button", { name: "conversation s3" });
    await waitFor(() => expect(opened()).toContain("s1"));
  }

  it("walks the sidebar's order with Ctrl+Tab and Ctrl+Shift+Tab, wrapping at the ends", async () => {
    // Pressed where focus actually is: in each conversation's composer, which takes focus as it
    // opens. Under the strict "never in a field" rule only the first press of a run worked.
    await ready();
    await waitFor(() => expect(screen.getByPlaceholderText(/^Ask about this code/)).toHaveFocus());
    key(ctrlTab);
    await waitFor(() => expect(last()).toBe("s2"));
    key(ctrlShiftTab);
    await waitFor(() => expect(last()).toBe("s1"));
    key(ctrlShiftTab);
    await waitFor(() => expect(last()).toBe("s3"));
    key(ctrlTab);
    await waitFor(() => expect(last()).toBe("s1"));
  });

  it("leaves the conversation alone when the composer holds a draft", async () => {
    const user = userEvent.setup({ delay: null });
    await ready();
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "half a message");
    const before = opened().length;
    key(ctrlTab);
    key(reopen);
    await act(async () => {});
    expect(opened()).toHaveLength(before);
  });

  it("reopens what was left, newest first, with Ctrl+Shift+T", async () => {
    const user = userEvent.setup({ delay: null });
    await ready();
    key(ctrlTab); // leaves s1 for s2
    await waitFor(() => expect(last()).toBe("s2"));
    await user.click(screen.getByRole("button", { name: "New conversation" })); // leaves s2
    (document.activeElement as HTMLElement | null)?.blur();

    key(reopen);
    await waitFor(() => expect(last()).toBe("s2"));
    key(reopen); // and under it, the one left before
    await waitFor(() => expect(last()).toBe("s1"));
  });

  it("reopens from the palette's command by name, the way a window menu would ask", async () => {
    await ready();
    key(ctrlTab);
    await waitFor(() => expect(last()).toBe("s2"));
    act(() => requestAppCommand("reopenConversation"));
    await waitFor(() => expect(last()).toBe("s1"));
  });

  it("finds in the transcript, steps with Enter, F3 and Shift+F3, and Escape clears it", async () => {
    const registry = new Map<string, { ranges: Range[] }>();
    vi.stubGlobal("CSS", { highlights: registry });
    vi.stubGlobal("Highlight", class { ranges: Range[]; constructor(...r: Range[]) { this.ranges = r; } });
    const user = userEvent.setup({ delay: null });
    await ready();
    await screen.findByText(/alpha beta ALPHA from s1/);

    key(find);
    const field = await screen.findByRole("searchbox", { name: "Find in conversation" });
    expect(field).toHaveFocus();
    await user.type(field, "alpha");
    expect(await screen.findByText("1 of 2")).toBeInTheDocument();
    expect(registry.get(FIND_HIGHLIGHT)?.ranges).toHaveLength(2);
    // Only the transcript: the composer and the field itself hold no matches.
    expect(registry.get(FIND_HIGHLIGHT)?.ranges.every((r) => r.toString().toLowerCase() === "alpha")).toBe(true);

    await user.keyboard("{Enter}");
    expect(screen.getByText("2 of 2")).toBeInTheDocument();
    expect(registry.get(FIND_CURRENT)?.ranges[0]).toBe(registry.get(FIND_HIGHLIGHT)?.ranges[1]);
    await user.keyboard("{F3}");
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    await user.keyboard("{Shift>}{F3}{/Shift}");
    expect(screen.getByText("2 of 2")).toBeInTheDocument();

    // F3 from outside the field still steps while the bar is open.
    field.blur();
    key({ key: "F3", code: "F3" });
    await waitFor(() => expect(screen.getByText("1 of 2")).toBeInTheDocument());

    field.focus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("searchbox", { name: "Find in conversation" })).not.toBeInTheDocument();
    expect(registry.size).toBe(0);
  });

  it("says when there is no match, and leaves Ctrl+F alone while typing in the composer", async () => {
    const user = userEvent.setup({ delay: null });
    await ready();
    const composer = screen.getByPlaceholderText(/^Ask about this code/);
    await user.type(composer, "draft");
    key(find);
    await act(async () => {});
    // By name: the sidebar has a search field of its own, always on screen.
    expect(screen.queryByRole("searchbox", { name: FIND_LABEL })).not.toBeInTheDocument();

    await user.clear(composer);
    key(find);
    await user.type(await screen.findByRole("searchbox", { name: FIND_LABEL }), "zebra");
    expect(await screen.findByText("No matches")).toBeInTheDocument();
  });
});
