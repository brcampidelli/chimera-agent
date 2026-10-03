import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation, TurnReceipt } from "@/components/code/Conversation";
import { getGitStatus, postSuggestionEvent, streamCodeTurn } from "@/lib/api";
import { DICTS } from "@/lib/i18n";
import type { OutputStyle } from "@/lib/types";
import { gitStatus, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function mount(over: { style?: OutputStyle; provider?: string } = {}) {
  return renderWithProviders(
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
      {...over}
    />,
  );
}

async function ask(text: string) {
  const box = await screen.findByRole("textbox");
  await userEvent.type(box, `${text}{Enter}`);
  await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
}

function events() {
  return vi.mocked(postSuggestionEvent).mock.calls.map(([e]) => e);
}

/**
 * Suggested next steps under the last answer (study 29, P4.5): they fill the box and never send.
 *
 * The order of assertions in each test is the order a person meets them — the chip, the click, the
 * box, the absence of a second turn, the send — because the promise is about what does NOT happen
 * between the click and the person's own Enter.
 */
describe("suggested next steps", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset();
    vi.mocked(postSuggestionEvent).mockClear();
    vi.mocked(getGitStatus).mockReset().mockResolvedValue(gitStatus());
    localStorage.clear();
  });

  it("fills the box with the open item and sends nothing until the person does", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({ todos: [[{ task: "read", status: "done" }, { task: "write the test", status: "pending" }]] }),
    );
    mount();
    await ask("start the refactor");

    const chips = await screen.findByRole("group", { name: /suggested next steps/i });
    await userEvent.click(within(chips).getByRole("button", { name: /Continue: write the test/ }));

    const box = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(box.value).toContain("write the test");
    // Filled, not sent: still the one turn the person sent.
    expect(streamCodeTurn).toHaveBeenCalledTimes(1);
    // A full box is a box a click would overwrite, so the chips step aside.
    expect(screen.queryByRole("group", { name: /suggested next steps/i })).not.toBeInTheDocument();

    expect(events()).toEqual([
      { event: "shown", kind: "continue", edited: false },
      { event: "picked", kind: "continue", edited: false },
    ]);

    await userEvent.type(box, "{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamCodeTurn).mock.calls[1][0]).toMatchObject({ message: expect.stringContaining("write the test") });
    // The second turn's answer offers its own chips (the script leaves the item open again), so the
    // send is looked for by event, not as the last line of the ledger.
    expect(events().filter((e) => e.event === "sent")).toEqual([{ event: "sent", kind: "continue", edited: false }]);
  });

  it("counts a send whose text was changed first as edited", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ todos: [[{ task: "ship it", status: "pending" }]] }));
    mount();
    await ask("go");

    await userEvent.click(await screen.findByRole("button", { name: /Continue: ship it/ }));
    await userEvent.type(screen.getByRole("textbox"), " carefully{Enter}");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(events().filter((e) => e.event === "sent")).toEqual([{ event: "sent", kind: "continue", edited: true }]);
  });

  it("does not count a message the person typed after emptying the box", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ todos: [[{ task: "ship it", status: "pending" }]] }));
    mount();
    await ask("go");

    await userEvent.click(await screen.findByRole("button", { name: /Continue: ship it/ }));
    const box = screen.getByRole("textbox");
    await userEvent.clear(box);
    await userEvent.type(box, "something else entirely{Enter}");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(events().map((e) => e.event)).not.toContain("sent");
  });

  it("offers to fix a failed check with the failure in the text", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({
        edits: [{ path: "src/a.py", patch: "@@" }],
        verified: { state: "failed", command: "pytest -q", source: "inferred", output: "E assert 3 == 4" },
      }),
    );
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ files: [{ path: "src/a.py", x: " ", y: "M", staged: false, untracked: false }] }));
    mount();
    await ask("make the sum 4");

    const chips = await screen.findByRole("group", { name: /suggested next steps/i });
    // A failed check offers the fix and NOT the commit: committing what the check rejected is the one
    // suggestion that could make things worse.
    expect(within(chips).getAllByRole("button")).toHaveLength(1);
    await userEvent.click(within(chips).getByRole("button", { name: /fix the failure/i }));
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toContain("assert 3 == 4");
    expect(streamCodeTurn).toHaveBeenCalledTimes(1);
  });

  it("offers a commit for the files the turn wrote that git still reports as changed", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({ edits: [{ path: "src/a.py", patch: "@@" }], verified: { state: "passed", command: "pytest", source: "inferred", output: "" } }),
    );
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus({ files: [{ path: "src/a.py", x: " ", y: "M", staged: false, untracked: false }] }));
    mount();
    await ask("tidy a.py");

    expect(await screen.findByRole("button", { name: /Commit a\.py/ })).toBeInTheDocument();
  });

  it("offers nothing after a turn with no open fact", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    mount();
    await ask("hello");

    await screen.findByText("done");
    expect(screen.queryByRole("group", { name: /suggested next steps/i })).not.toBeInTheDocument();
    expect(getGitStatus).not.toHaveBeenCalled();
    expect(events()).toEqual([]);
  });
});

/**
 * The style chip's half of the request (the chip itself lives on the Code screen). The default must
 * leave the request exactly as it was: the field is absent, not "default".
 */
describe("the output style on the request", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockImplementation(scriptTurn());
    localStorage.clear();
  });

  async function sentWith(over: { style?: OutputStyle; provider?: string }) {
    vi.mocked(streamCodeTurn).mockClear();
    const view = mount(over);
    await ask("explain the parser");
    const req = vi.mocked(streamCodeTurn).mock.calls[0][0];
    view.unmount();
    return req;
  }

  it("sends byte for byte the same request for the default as for no style at all", async () => {
    const before = await sentWith({});
    const named = await sentWith({ style: "default" });
    expect(JSON.stringify(named)).toBe(JSON.stringify(before));
    expect(before).not.toHaveProperty("style");
  });

  it("sends a chosen style, and none to an external agent", async () => {
    expect(await sentWith({ style: "concise" })).toMatchObject({ style: "concise" });
    expect(await sentWith({ style: "explanatory", provider: "claude" })).not.toHaveProperty("style");
  });
});

describe("the receipt", () => {
  const done = {
    answer: "", steps: 1, stopped_reason: "final", tool_names: [], model: "m", prompt_tokens: 0,
    completion_tokens: 0, usd: 0, context_peak_tokens: 0, route_meta: null,
  };
  const t = (key: string, p?: Record<string, string | number>) =>
    (DICTS.en[key] ?? key).replace(/\{(\w+)\}/g, (_m, name: string) => String(p?.[name] ?? ""));

  it("names the style the turn ran under, and nothing for a turn that carried none", () => {
    const styled = renderWithProviders(<TurnReceipt done={{ ...done, style: "explanatory", style_version: 1 }} t={t} />);
    expect(screen.getByText("style: Explanatory")).toBeInTheDocument();
    styled.unmount();
    renderWithProviders(<TurnReceipt done={done} t={t} />);
    expect(screen.queryByText(/^style:/)).not.toBeInTheDocument();
  });
});
