import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import {
  getCodeSession,
  listShares,
  listWorks,
  streamCodeTurn,
  streamSessionLive,
  type CodeTurnHandlers,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

// The markdown is replaced by a probe that records every render with the text it was given. That is
// the cost this work removes — re-parsing an answer that did not change — so it is what the tests count.
const probe = vi.hoisted(() => ({ renders: [] as string[] }));
vi.mock("@/components/markdown/SafeMarkdown", () => ({
  SafeMarkdown: ({ children }: { children: string }) => {
    probe.renders.push(String(children));
    return <div data-testid="md">{children}</div>;
  },
}));

// Counts renders of the exchange itself. The clock is a child only `ExchangeView` renders, and each
// finished exchange carries a distinct `elapsed_seconds`, so a render of the clock is a render of
// that exchange. The markdown probe above cannot see this: `AnswerMarkdown` has its own memo and
// would stay quiet even if `ExchangeView` lost the stability of its props.
const clock = vi.hoisted(() => ({ renders: [] as Array<number | undefined> }));
vi.mock("@/components/code/Elapsed", () => ({
  Elapsed: ({ finalSeconds }: { finalSeconds?: number }) => {
    clock.renders.push(finalSeconds);
    return <span data-testid="turn-elapsed">{finalSeconds}</span>;
  },
}));

/**
 * Streaming: one write per frame, in order, without touching the exchanges that did not move.
 *
 * The frame clock is replaced by a queue the test runs by hand, so "a frame" is something it can
 * count rather than a 16 ms wait it can only hope for.
 */
function mount() {
  return renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      resumeSession="s1"
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
}

const DONE = {
  answer: "",
  steps: 0,
  stopped_reason: "",
  tool_names: [],
  model: "",
  prompt_tokens: 0,
  completion_tokens: 0,
  usd: null,
  tainted: false,
  memory_facts_used: 0,
  memory_layer: "",
  fused: false,
} as never;

function stored(n: number) {
  return Array.from({ length: n }, (_, k) => ({
    you: `question-${k + 1}`,
    answer: `stored-answer-${k + 1}`,
    tools: [],
    edits: [],
    done: { ...(DONE as object), answer: `stored-answer-${k + 1}`, elapsed_seconds: 100 + k },
  }));
}

let frames: Map<number, FrameRequestCallback>;
let nextFrame: number;
let handlers: CodeTurnHandlers;
let finishStream: () => void;

/** Run every queued frame, the way the browser does once per paint. */
function runFrame() {
  const due = [...frames.values()];
  frames.clear();
  act(() => {
    for (const cb of due) cb(0);
  });
}

async function startTurn() {
  vi.mocked(streamCodeTurn).mockImplementation(
    (_req: unknown, h: CodeTurnHandlers) =>
      new Promise<void>((resolve) => {
        handlers = h;
        finishStream = resolve;
        h.onSession?.("s1", "t-live", 1);
      }),
  );
  mount();
  await screen.findByText("question-4");
  const box = await screen.findByRole("textbox");
  await userEvent.type(box, "go{Enter}");
  await waitFor(() => expect(handlers).toBeDefined());
}

function mdTexts(): string[] {
  return screen.getAllByTestId("md").map((node) => node.textContent ?? "");
}

/** The text of the last answer on screen: the active exchange's. */
function lastMd(): string {
  const all = mdTexts();
  return all[all.length - 1];
}

describe("streaming into the conversation", () => {
  beforeEach(() => {
    frames = new Map();
    nextFrame = 1;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => {
      const id = nextFrame++;
      frames.set(id, cb);
      return id;
    });
    vi.spyOn(window, "cancelAnimationFrame").mockImplementation((id) => {
      frames.delete(id);
    });
    probe.renders.length = 0;
    clock.renders.length = 0;
    vi.mocked(streamCodeTurn).mockReset();
    vi.mocked(streamSessionLive).mockReset().mockResolvedValue(null);
    vi.mocked(listShares).mockReset().mockResolvedValue({ shares: [] });
    vi.mocked(listWorks).mockReset().mockResolvedValue({ works: [] });
    vi.mocked(getCodeSession)
      .mockReset()
      .mockResolvedValue({ id: "s1", workspace: "/proj", exchanges: stored(4) } as never);
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  it("does not re-render the exchanges that did not move while 200 tokens stream into the active one", async () => {
    await startTurn();
    // 4 finished exchanges + the one just sent = 5.
    expect(screen.getByText("question-4")).toBeInTheDocument();
    act(() => handlers.onToken?.("first "));
    runFrame();
    await waitFor(() => expect(mdTexts()).toContain("first "));

    const before = probe.renders.filter((text) => text.startsWith("stored-answer-")).length;
    expect(before).toBeGreaterThanOrEqual(4);
    const finishedClock = () => clock.renders.filter((s) => s !== undefined && s >= 100 && s < 104).length;
    const clocksBefore = finishedClock();
    expect(clocksBefore).toBeGreaterThanOrEqual(4);

    for (let n = 0; n < 200; n += 1) {
      act(() => handlers.onToken?.("x"));
      if (n % 4 === 3) runFrame();
    }
    runFrame();

    // The exchanges themselves, not just their markdown: none of the four was rendered again.
    expect(finishedClock()).toBe(clocksBefore);
    // The four finished answers were parsed again 0 times; the active one moved on.
    expect(probe.renders.filter((text) => text.startsWith("stored-answer-")).length).toBe(before);
    expect(lastMd()).toBe(`first ${"x".repeat(200)}`);
  });

  it("writes 50 tokens that arrive inside one frame as one update, and loses none of them", async () => {
    await startTurn();
    act(() => handlers.onToken?.("start:"));
    runFrame();
    const settled = probe.renders.length;

    const pieces = Array.from({ length: 50 }, (_, k) => `${k},`);
    act(() => {
      for (const piece of pieces) handlers.onToken?.(piece);
    });
    // Nothing is drawn until the frame: the 50 are held, not written one by one.
    expect(probe.renders.length).toBe(settled);

    runFrame();
    expect(probe.renders.length).toBe(settled + 1);
    expect(lastMd()).toBe(`start:${pieces.join("")}`);
  });

  it("puts the text that came before a tool frame in the exchange before the tool", async () => {
    await startTurn();
    act(() => {
      handlers.onToken?.("let me ");
      handlers.onToken?.("look");
      // No frame has run: the tool frame itself must bring the held text in first.
      handlers.onTool?.({ name: "read_file", arguments: { path: "a.py" }, ok: true, observation: "" });
    });
    expect(lastMd()).toBe("let me look");
    expect(screen.getAllByText(/read_file/).length).toBeGreaterThan(0);

    act(() => handlers.onToken?.(" then more"));
    runFrame();
    expect(lastMd()).toBe("let me look then more");
  });

  it("keeps the last piece when the turn ends or fails before a frame", async () => {
    await startTurn();
    act(() => {
      handlers.onToken?.("tail ");
      handlers.onToken?.("end");
      handlers.onDone?.({ ...(DONE as object), answer: "" } as never);
    });
    expect(lastMd()).toBe("tail end");
    // A frame that was already queued finds nothing left to write: no second copy of the text.
    runFrame();
    expect(lastMd()).toBe("tail end");
    act(() => finishStream());
  });

  it("keeps the last piece when the turn fails before a frame", async () => {
    await startTurn();
    act(() => {
      handlers.onToken?.("partial ");
      handlers.onToken?.("answer");
      handlers.onError?.("boom", undefined as never);
    });
    expect(lastMd()).toBe("partial answer");
    runFrame();
    expect(lastMd()).toBe("partial answer");
    act(() => finishStream());
  });

  it("keeps the held text when the turn is abandoned before a frame", async () => {
    await startTurn();
    act(() => {
      handlers.onToken?.("a ");
      handlers.onToken?.("b");
    });
    // No frame has run: the Stop click itself must bring the held text in before it marks the exchange.
    await userEvent.click(screen.getByRole("button", { name: /stop/i }));
    expect(lastMd()).toBe("a b");
    expect(screen.getByText(/You stopped this turn/)).toBeInTheDocument();
    // A frame queued before the click finds nothing left to write: the text does not appear twice.
    runFrame();
    expect(lastMd()).toBe("a b");
    // A token that arrives after the abandon is dropped, not appended to the stopped exchange.
    act(() => handlers.onToken?.(" late"));
    runFrame();
    expect(lastMd()).toBe("a b");
    act(() => finishStream());
  });

  it("writes tokens at once while the window is hidden, without waiting for a frame", async () => {
    await startTurn();
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    // No frame is run here: with the window hidden none would come.
    act(() => handlers.onToken?.("while away"));
    expect(lastMd()).toBe("while away");
  });
});
