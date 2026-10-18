import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { matchScore, mentionQuery } from "@/components/code/MentionMenu";
import {
  findPaths,
  listCodeCommands,
  listCodeSessions,
  listDesigns,
  streamCodeTurn,
} from "@/lib/api";
import { MAX_CONTEXT_ITEMS, isProjectPath, requestAddContext } from "@/lib/context-items";
import { renderWithProviders } from "@/test/utils";

// The list opens after the project's paths load and the query settles. On a loaded CI runner that
// took over the 1 s default of `findBy*` (main, 18/10: "Unable to find role=listbox"), while the same
// test passes in ~1.1 s locally. The wait is for the list to appear, not a looser assertion.
const LIST_WAIT = { timeout: 5000 } as const;

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The composer's `@` list (study 34, gap #2): files and folders of the project, designs and recent
 * conversations, picked from the keyboard, shown as chips, and sent as `context` with the turn.
 *
 * What is pinned: nothing is asked for until an `@` is typed; the list filters and moves with the
 * keyboard; Enter makes a chip WITHOUT sending; a chip can be removed; the turn carries each chip as
 * a typed item and omits the field when there are none; a path outside the project never becomes a
 * chip; the count is capped; and the `/` list still behaves as it did.
 */

function mount() {
  renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      posture={{ reach: "workspace" as never, approval: "ask" as never }}
      profile={"balanced" as never}
      onHandOff={() => {}}
      onBatch={() => {}}
      onEdited={() => {}}
      busyElsewhere={false}
      controls={null}
      onOpenFile={() => {}}
    />,
  );
  return screen.findByRole("textbox");
}

function sentRequest(): Record<string, unknown> {
  return vi.mocked(streamCodeTurn).mock.calls[vi.mocked(streamCodeTurn).mock.calls.length - 1]?.[0] as unknown as Record<string, unknown>;
}

describe("which words are mentions", () => {
  it("is the @ word under the caret, at the start or after a space", () => {
    expect(mentionQuery("@", 1)).toEqual({ query: "", start: 0 });
    expect(mentionQuery("fix @log", 8)).toEqual({ query: "log", start: 4 });
    expect(mentionQuery("fix @log now", 8)).toEqual({ query: "log", start: 4 });
    expect(mentionQuery("fix @log now", 12)).toBeNull(); // the caret has left the word
    expect(mentionQuery("mail bob@example.com", 20)).toBeNull(); // an address is not a mention
    expect(mentionQuery("no at sign", 10)).toBeNull();
  });

  it("matches letters in order and prefers a prefix", () => {
    expect(matchScore("lgn", "Login page")).not.toBeNull();
    expect(matchScore("xyz", "Login page")).toBeNull();
    expect(matchScore("log", "Login")! > matchScore("log", "Catalog")!).toBe(true);
  });

  it("keeps paths inside the project", () => {
    expect(isProjectPath("src/a.ts")).toBe(true);
    expect(isProjectPath("../a.ts")).toBe(false);
    expect(isProjectPath("/etc/passwd")).toBe(false);
    expect(isProjectPath("C:\\Windows\\win.ini")).toBe(false);
    expect(isProjectPath("src/../../a")).toBe(false);
  });
});

describe("the composer's @ list", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
    vi.mocked(findPaths).mockReset().mockImplementation(async (_ws, query) => ({
      capped: false,
      entries: [
        { path: "src/auth/login.py", is_dir: false },
        { path: "src/auth", is_dir: true },
      ].filter((e) => e.path.includes(query)),
    }));
    vi.mocked(listDesigns).mockReset().mockResolvedValue([
      { id: "d1", title: "Login screen", updated_at: "2026-10-01T00:00:00Z", revision: 3 },
    ]);
    vi.mocked(listCodeSessions).mockReset().mockResolvedValue([
      { id: "c1", title: "Login bug hunt", workspace: "/proj", turns: 4, updated_at: 20 },
      { id: "c2", title: "Release notes", workspace: "/proj", turns: 1, updated_at: 10 },
    ] as never);
  });

  it("asks for nothing until an @ starts a word", async () => {
    const box = await mount();
    await userEvent.type(box, "fix the login");
    expect(findPaths).not.toHaveBeenCalled();
    expect(listDesigns).not.toHaveBeenCalled();
    expect(listCodeSessions).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("lists files, folders, designs and conversations, grouped and filtered", async () => {
    const box = await mount();
    await userEvent.type(box, "fix @login");
    const list = await screen.findByRole("listbox", { name: "Mention" }, LIST_WAIT);
    await waitFor(() => expect(within(list).getAllByRole("option")).toHaveLength(3));
    expect(within(list).getByRole("group", { name: "Files and folders" })).toBeInTheDocument();
    expect(within(list).getByRole("group", { name: "Designs" })).toBeInTheDocument();
    expect(within(list).getByRole("group", { name: "Conversations" })).toBeInTheDocument();
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "src/auth/login.py",
      "Login screen",
      "Login bug hunt",
    ]);
    expect(findPaths).toHaveBeenLastCalledWith("/proj", "login", 8);
    expect(box).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Matches: 3");
  });

  it("moves with the arrows and makes a chip on Enter, without sending", async () => {
    const box = await mount();
    await userEvent.type(box, "fix @auth");
    const list = await screen.findByRole("listbox", { name: "Mention" }, LIST_WAIT);
    await waitFor(() => expect(within(list).getAllByRole("option").length).toBeGreaterThan(1));
    const options = () => within(list).getAllByRole("option");
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(options()[1]).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", options()[1].id);
    expect(options()[1]).toHaveTextContent("src/auth/");
    await userEvent.keyboard("{Enter}");
    expect(streamCodeTurn).not.toHaveBeenCalled();
    expect(box).toHaveValue("fix ");
    expect(screen.queryByRole("listbox", { name: "Mention" })).not.toBeInTheDocument();
    const chips = screen.getByRole("list", { name: "Mentioned in this message" });
    expect(within(chips).getByText("src/auth/")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Mentioned: src/auth/");
  });

  it("closes on Escape, and Enter then sends the message as typed", async () => {
    const box = await mount();
    await userEvent.type(box, "ping @login");
    await screen.findByRole("listbox", { name: "Mention" }, LIST_WAIT);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox", { name: "Mention" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(sentRequest().message).toBe("ping @login");
    expect(sentRequest()).not.toHaveProperty("context");
  });

  it("sends every chip as a typed item with the turn, then clears them", { timeout: 20000 }, async () => {
    const box = await mount();
    await userEvent.type(box, "@login");
    let list = await screen.findByRole("listbox", { name: "Mention" }, LIST_WAIT);
    await waitFor(() => expect(within(list).getAllByRole("option")).toHaveLength(3));
    await userEvent.keyboard("{Enter}"); // the file
    await userEvent.type(box, "@login");
    list = await screen.findByRole("listbox", { name: "Mention" }, LIST_WAIT);
    await waitFor(() => expect(within(list).getAllByRole("option")).toHaveLength(3));
    await userEvent.keyboard("{ArrowDown}{Enter}"); // the design
    await userEvent.type(box, "@bug");
    list = await screen.findByRole("listbox", { name: "Mention" }, LIST_WAIT);
    await waitFor(() => expect(within(list).getAllByRole("option")).toHaveLength(1));
    await userEvent.keyboard("{Enter}"); // the conversation
    await userEvent.type(box, "fix these{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(sentRequest().message).toBe("fix these");
    expect(sentRequest().context).toEqual([
      { kind: "file", path: "src/auth/login.py" },
      { kind: "design", id: "d1" },
      { kind: "conversation", id: "c1" },
    ]);
    expect(screen.queryByRole("list", { name: "Mentioned in this message" })).not.toBeInTheDocument();
  });

  it("removes a chip with its button, and the turn no longer carries it", async () => {
    const box = await mount();
    await userEvent.type(box, "@login");
    const list = await screen.findByRole("listbox", { name: "Mention" }, LIST_WAIT);
    await waitFor(() => expect(within(list).getAllByRole("option")).toHaveLength(3));
    await userEvent.keyboard("{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Remove mention src/auth/login.py" }));
    expect(screen.queryByRole("list", { name: "Mentioned in this message" })).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("No longer mentioned: src/auth/login.py");
    await userEvent.type(box, "go{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(sentRequest()).not.toHaveProperty("context");
  });

  it("refuses a path outside the project from another screen, and says so", async () => {
    await mount();
    act(() => requestAddContext({ item: { kind: "file", path: "../secrets.env" }, label: "../secrets.env" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Not mentioned — outside this project: ../secrets.env");
    expect(screen.queryByRole("list", { name: "Mentioned in this message" })).not.toBeInTheDocument();
  });

  it("takes a line range from another screen as a chip and sends it", async () => {
    const box = await mount();
    act(() =>
      requestAddContext({
        item: { kind: "file", path: "src/app.ts", start_line: 3, end_line: 9 },
        label: "src/app.ts",
      }),
    );
    expect(await screen.findByText("src/app.ts (lines 3–9)")).toBeInTheDocument();
    await userEvent.type(box, "explain{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(sentRequest().context).toEqual([{ kind: "file", path: "src/app.ts", start_line: 3, end_line: 9 }]);
  });

  it("stops at the cap and says why", async () => {
    await mount();
    act(() => {
      for (let i = 0; i <= MAX_CONTEXT_ITEMS; i += 1) {
        requestAddContext({ item: { kind: "folder", path: `d${i}` }, label: `d${i}/` });
      }
    });
    const chips = await screen.findByRole("list", { name: "Mentioned in this message" });
    expect(within(chips).getAllByRole("listitem")).toHaveLength(MAX_CONTEXT_ITEMS);
    expect(screen.getByRole("alert")).toHaveTextContent(`Up to ${MAX_CONTEXT_ITEMS} mentions per message`);
  });

  it("leaves the / list as it was", async () => {
    vi.mocked(listCodeCommands).mockReset().mockResolvedValue({
      commands: [{ name: "review", description: "Review a file", source: "project", argument_hint: "", error: null }],
      reserved: [],
    });
    const box = await mount();
    await userEvent.type(box, "/rev");
    await screen.findByRole("listbox", { name: "Commands" });
    expect(screen.queryByRole("listbox", { name: "Mention" })).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    expect(box).toHaveValue("/review ");
    await userEvent.type(box, "src/a.py{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(sentRequest().message).toBe("/review src/a.py");
    expect(findPaths).not.toHaveBeenCalled();
  });
});
