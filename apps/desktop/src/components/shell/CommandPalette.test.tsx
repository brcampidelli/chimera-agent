import { useState } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CommandPalette, type Command } from "@/components/shell/CommandPalette";
import { I18nProvider } from "@/lib/i18n";

function makeCommands(run = vi.fn()): Command[] {
  return [
    { id: "go-chat", label: "Chat", group: "Go to", run },
    { id: "go-code", label: "Code", group: "Go to", run },
    { id: "s-1", label: "Refactor the parser", group: "Conversation", run },
  ];
}

function Harness({ commands }: { commands: Command[] }) {
  const [open, setOpen] = useState(false);
  return (
    <I18nProvider>
      <button onClick={() => setOpen(true)}>Open palette</button>
      <CommandPalette open={open} onOpenChange={setOpen} commands={commands} />
    </I18nProvider>
  );
}

async function openPalette() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Open palette" }));
  await screen.findByRole("combobox");
  return user;
}

describe("CommandPalette", () => {
  it("announces itself as a combobox over a listbox", async () => {
    render(<Harness commands={makeCommands()} />);
    await openPalette();
    // Hand-built, so the semantics have to be deliberate: without these a screen reader announces a
    // plain text field and never mentions the list underneath it.
    const input = screen.getByRole("combobox");
    expect(input).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-activedescendant", "cmd-go-chat");
  });

  it("filters by label and by group", async () => {
    render(<Harness commands={makeCommands()} />);
    const user = await openPalette();

    await user.type(screen.getByRole("combobox"), "parser");
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(screen.getByRole("option")).toHaveTextContent("Refactor the parser");

    await user.clear(screen.getByRole("combobox"));
    // Searching by what a thing IS, not only by its name: "conversation" should find conversations.
    await user.type(screen.getByRole("combobox"), "conversation");
    expect(screen.getAllByRole("option")).toHaveLength(1);
  });

  it("moves through results with the arrow keys and wraps", async () => {
    render(<Harness commands={makeCommands()} />);
    const user = await openPalette();
    const input = screen.getByRole("combobox");

    await user.keyboard("{ArrowDown}");
    expect(input).toHaveAttribute("aria-activedescendant", "cmd-go-code");
    await user.keyboard("{ArrowUp}{ArrowUp}");
    expect(input).toHaveAttribute("aria-activedescendant", "cmd-s-1");
  });

  it("runs the highlighted command on Enter and closes", async () => {
    const run = vi.fn();
    render(<Harness commands={makeCommands(run)} />);
    const user = await openPalette();

    await user.keyboard("{ArrowDown}{Enter}");
    expect(run).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());
  });

  it("starts clean each time it opens", async () => {
    render(<Harness commands={makeCommands()} />);
    const user = await openPalette();

    await user.type(screen.getByRole("combobox"), "code");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Open palette" }));
    // Reopening onto a stale query is disorienting, and a stale index can point past the end of a
    // shorter list.
    expect(await screen.findByRole("combobox")).toHaveValue("");
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });

  it("says so when nothing matches, rather than showing an empty box", async () => {
    render(<Harness commands={makeCommands()} />);
    const user = await openPalette();
    await user.type(screen.getByRole("combobox"), "zzzz");
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText(/nothing matches/i)).toBeInTheDocument();
  });

  it("does nothing on Enter when nothing matches", async () => {
    const run = vi.fn();
    render(<Harness commands={makeCommands(run)} />);
    const user = await openPalette();
    await user.type(screen.getByRole("combobox"), "zzzz");
    await user.keyboard("{Enter}");
    expect(run).not.toHaveBeenCalled();
  });
});
