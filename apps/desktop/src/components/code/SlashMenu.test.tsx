import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation, TurnReceipt } from "@/components/code/Conversation";
import { matchCommands, slashQuery } from "@/components/code/SlashMenu";
import { listCodeCommands, streamCodeTurn, type CodeTurnDone } from "@/lib/api";
import type { PromptCommand } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The composer's `/` list (plugin plan B7): the folder's Markdown commands, offered as you type.
 *
 * They worked only in the terminal, so a command written in a repository was invisible from the app.
 * What is pinned: the list opens on a bare `/word`, filters, moves with the keyboard, and Enter puts
 * `/name ` in the box WITHOUT sending — expansion is the server's, when the turn goes out. And the
 * receipt names the command a turn was expanded from.
 */
const COMMANDS: PromptCommand[] = [
  { name: "review", description: "Review a file", source: "project", argument_hint: "<path>", error: null },
  { name: "release", description: "Cut a release", source: "owner", argument_hint: "", error: null },
  { name: "my-plugin:hello", description: "Say hello", source: "plugin:my-plugin", argument_hint: "<who>", error: null },
  { name: "broken", description: "", source: "project", argument_hint: "", error: "invalid frontmatter" },
];

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

describe("which drafts open the list", () => {
  it("opens only on a bare slash word", () => {
    expect(slashQuery("/")).toBe("");
    expect(slashQuery("/rev")).toBe("rev");
    expect(slashQuery("/review src")).toBeNull(); // the name is chosen; arguments follow
    expect(slashQuery("fix /review")).toBeNull();
    expect(slashQuery("")).toBeNull();
  });

  it("puts name-prefix matches before names that only contain the query", () => {
    const names = matchCommands(COMMANDS, "re").map((c) => c.name);
    expect(names).toEqual(["review", "release"]);
    expect(matchCommands(COMMANDS, "hello").map((c) => c.name)).toEqual(["my-plugin:hello"]);
  });
});

describe("the composer's slash list", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
    vi.mocked(listCodeCommands).mockReset().mockResolvedValue({ commands: COMMANDS, reserved: ["undo"] });
    localStorage.clear();
  });

  it("is not asked for until a slash starts the box", async () => {
    const box = await mount();
    await userEvent.type(box, "hello");
    expect(listCodeCommands).not.toHaveBeenCalled();
    await userEvent.clear(box);
    await userEvent.type(box, "/");
    await waitFor(() => expect(listCodeCommands).toHaveBeenCalledWith("/proj"));
  });

  it("lists the commands with their description and source", async () => {
    const box = await mount();
    await userEvent.type(box, "/");
    const list = await screen.findByRole("listbox", { name: "Commands" });
    const options = within(list).getAllByRole("option");
    expect(options).toHaveLength(4);
    expect(within(options[0]).getByText("/review")).toBeInTheDocument();
    expect(within(options[0]).getByText("<path>")).toBeInTheDocument();
    expect(within(options[0]).getByText("Review a file")).toBeInTheDocument();
    expect(within(options[0]).getByText("project")).toBeInTheDocument();
    expect(within(options[1]).getByText("yours")).toBeInTheDocument();
    expect(within(options[2]).getByText("plugin my-plugin")).toBeInTheDocument();
    expect(within(options[3]).getByText("Broken: invalid frontmatter")).toBeInTheDocument();
    expect(box).toHaveAttribute("aria-expanded", "true");
  });

  it("filters as you type", async () => {
    const box = await mount();
    await userEvent.type(box, "/rel");
    const list = await screen.findByRole("listbox");
    expect(within(list).getAllByRole("option").map((o) => o.textContent)).toEqual([
      expect.stringContaining("/release"),
    ]);
  });

  it("moves with the arrows and inserts the chosen name on Enter, without sending", async () => {
    const box = await mount();
    await userEvent.type(box, "/");
    const list = await screen.findByRole("listbox");
    const options = () => within(list).getAllByRole("option");
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
    await userEvent.keyboard("{ArrowDown}");
    expect(options()[1]).toHaveAttribute("aria-selected", "true");
    expect(box).toHaveAttribute("aria-activedescendant", options()[1].id);
    await userEvent.keyboard("{ArrowUp}{ArrowUp}");
    expect(options()[3]).toHaveAttribute("aria-selected", "true"); // wraps
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(box).toHaveValue("/review ");
    expect(streamCodeTurn).not.toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("sends the line as typed once the name is chosen — expansion is the server's", async () => {
    const box = await mount();
    await userEvent.type(box, "/rev");
    await screen.findByRole("listbox");
    await userEvent.keyboard("{Tab}");
    await userEvent.type(box, "src/app.py{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    const sent = vi.mocked(streamCodeTurn).mock.calls[0]?.[0] as { message: string };
    expect(sent.message).toBe("/review src/app.py");
  });

  it("closes on Escape, and Enter then sends the message", async () => {
    const box = await mount();
    await userEvent.type(box, "/review");
    await screen.findByRole("listbox");
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
  });

  it("stays closed when nothing matches, so Enter still sends", async () => {
    const box = await mount();
    await userEvent.type(box, "/zzz");
    await waitFor(() => expect(listCodeCommands).toHaveBeenCalled());
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
  });

  it("inserts on a click too", async () => {
    const box = await mount();
    await userEvent.type(box, "/");
    const list = await screen.findByRole("listbox");
    await userEvent.click(within(list).getByText("/my-plugin:hello"));
    expect(box).toHaveValue("/my-plugin:hello ");
  });
});

describe("the receipt of a command turn", () => {
  const BASE: CodeTurnDone = {
    answer: "ok",
    steps: 1,
    stopped_reason: "final",
    tool_names: [],
    model: "m",
    prompt_tokens: 10,
    completion_tokens: 5,
    usd: 0.001,
    route_meta: null,
    context_peak_tokens: 0,
  };

  it("names the command the message was expanded from, with its source", () => {
    renderWithProviders(
      <TurnReceipt
        done={{ ...BASE, command: { name: "my-plugin:hello", source: "plugin:my-plugin" } }}
        t={(k, a) => (a ? `${k}:${String(a.name)}` : k)}
      />,
    );
    const badge = screen.getByText("code.chat.command:my-plugin:hello");
    expect(badge.closest("[title]")).toHaveAttribute("title", "definitions.source.plugin:my-plugin");
  });

  it("says nothing on a turn sent as typed", () => {
    renderWithProviders(<TurnReceipt done={BASE} t={(k) => k} />);
    expect(screen.queryByText(/code\.chat\.command/)).not.toBeInTheDocument();
  });
});
