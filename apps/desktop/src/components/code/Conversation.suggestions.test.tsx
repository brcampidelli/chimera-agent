import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation, TurnReceipt } from "@/components/code/Conversation";
import { getCodeSession, getGitUncommitted, postSuggestionEvent, streamCodeTurn, type CodeTurnHandlers } from "@/lib/api";
import { DICTS } from "@/lib/i18n";
import type { OutputStyle } from "@/lib/types";
import { scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function mount(over: { style?: OutputStyle; provider?: string; resumeSession?: string } = {}) {
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
    vi.mocked(getGitUncommitted).mockReset().mockResolvedValue({ is_repo: false, files: [] });
    vi.mocked(getCodeSession).mockReset().mockResolvedValue({ id: "s1", workspace: "/w", exchanges: [] });
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
    vi.mocked(getGitUncommitted).mockResolvedValue({ is_repo: true, files: ["src/a.py"] });
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
    vi.mocked(getGitUncommitted).mockResolvedValue({ is_repo: true, files: ["src/a.py"] });
    mount();
    await ask("tidy a.py");

    expect(await screen.findByRole("button", { name: /Commit a\.py/ })).toBeInTheDocument();
    // The server is asked about the turn's own files, as the agent named them, in this workspace —
    // it matches them against git where the workspace and the repository root are both known.
    expect(getGitUncommitted).toHaveBeenCalledWith("/proj", ["src/a.py"]);
  });

  it("offers nothing after a turn with no open fact", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    mount();
    await ask("hello");

    await screen.findByText("done");
    expect(screen.queryByRole("group", { name: /suggested next steps/i })).not.toBeInTheDocument();
    expect(getGitUncommitted).not.toHaveBeenCalled();
    expect(events()).toEqual([]);
  });

  it("counts a pick once per offer, however often the box is emptied and the chip clicked again", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ todos: [[{ task: "ship it", status: "pending" }]] }));
    mount();
    await ask("go");

    const box = screen.getByRole("textbox");
    for (let i = 0; i < 3; i++) {
      await userEvent.click(await screen.findByRole("button", { name: /Continue: ship it/ }));
      await userEvent.clear(box);
    }

    // One offer, taken: a pick per click read "3 of 1 picked", a rate over 100%.
    expect(events().filter((e) => e.event === "picked")).toHaveLength(1);
    expect(events().filter((e) => e.event === "shown")).toHaveLength(1);
  });

  it("offers nothing, and counts nothing, under a turn read back from a reopened conversation", async () => {
    // The last stored turn failed its check: chips offered here were counted as shown again on every
    // reopen (the screen remounts with no memory of what it counted), and every app start reopens.
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s9",
      workspace: "/proj",
      exchanges: [
        {
          you: "make the sum 4",
          answer: "done",
          tools: [],
          edits: [{ path: "src/a.py", patch: "@@" }],
          done: { answer: "done", steps: 1, stopped_reason: "final", tool_names: [], model: "m", prompt_tokens: 0, completion_tokens: 0, usd: null, context_peak_tokens: 0, route_meta: null },
          verified: { state: "failed", command: "pytest -q", source: "inferred", output: "E assert 3 == 4" },
        },
      ],
    } as never);
    for (let i = 0; i < 2; i++) {
      const view = mount({ resumeSession: "s9" });
      expect(await screen.findByText("make the sum 4")).toBeInTheDocument();
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.queryByRole("group", { name: /suggested next steps/i })).not.toBeInTheDocument();
      view.unmount();
    }
    expect(events().filter((e) => e.event === "shown")).toEqual([]);
  });

  it("counts nothing as shown for the moment before the app continues a turn by itself", async () => {
    // The first turn stops at the step limit with an item open; "continue" is then sent by the app.
    // For the one commit between the two the chips used to be drawn, and a "shown" counted for an
    // offer nobody could take.
    localStorage.setItem("chimera.autoContinue", "1");
    let call = 0;
    vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
      call += 1;
      const first = call === 1;
      await new Promise((r) => setTimeout(r, 25));
      if (first) h.onTodo?.([{ task: "ship it", status: "pending" }]);
      h.onDone?.({
        answer: "done", steps: 1, stopped_reason: first ? "max_steps" : "final", tool_names: [], model: "m",
        prompt_tokens: 0, completion_tokens: 0, usd: null, context_peak_tokens: 0, route_meta: null,
      });
    });
    mount();
    await ask("start");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2), { timeout: 5000 });
    await new Promise((r) => setTimeout(r, 60));
    expect(events().filter((e) => e.event === "shown")).toEqual([]);
  });

  it("counts nothing as shown for the moment before a queued follow-up goes out", async () => {
    let release: () => void = () => {};
    let call = 0;
    vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
      call += 1;
      const first = call === 1;
      if (first) await new Promise<void>((r) => (release = r));
      if (first) h.onTodo?.([{ task: "ship it", status: "pending" }]);
      h.onDone?.({
        answer: "done", steps: 1, stopped_reason: "final", tool_names: [], model: "m",
        prompt_tokens: 0, completion_tokens: 0, usd: null, context_peak_tokens: 0, route_meta: null,
      });
    });
    mount();
    await ask("start");
    // Typed while the turn runs: queued, and sent by the app the moment the turn ends.
    await userEvent.type(screen.getByRole("textbox"), "and then the docs{Enter}");
    release();

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2), { timeout: 5000 });
    await new Promise((r) => setTimeout(r, 60));
    expect(events().filter((e) => e.event === "shown")).toEqual([]);
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
