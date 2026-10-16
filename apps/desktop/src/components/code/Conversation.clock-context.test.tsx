import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation, formatElapsed } from "@/components/code/Conversation";
import { streamCodeTurn, type CodeTurnDone, type CodeTurnHandlers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

// The step and turn clocks count from the SERVER's start (`started_at` on the opening frame and on
// `tool_start`) and stop on the server's measured duration. A clock counted from when this screen
// heard about the step would show a turn it came back to, or a guest's, as just begun.

function mount() {
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
    />,
  );
}

function done(extra: Partial<CodeTurnDone> = {}): CodeTurnDone {
  return {
    answer: "done", steps: 1, stopped_reason: "final", tool_names: [], model: "",
    prompt_tokens: 0, completion_tokens: 0, usd: null, tainted: false, memory_facts_used: 0,
    memory_layer: "", fused: false, ...extra,
  } as unknown as CodeTurnDone;
}

/** A turn that runs `before`, waits until the test releases it, then runs `after`. */
function heldTurn(
  before: (h: CodeTurnHandlers) => void,
  after: (h: CodeTurnHandlers) => void,
): { release: () => Promise<void> } {
  let release: () => void = () => {};
  vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
    before(h);
    await new Promise<void>((resolve) => { release = resolve; });
    after(h);
  });
  return { release: async () => { await act(async () => { release(); }); } };
}

async function send(text: string) {
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
  await user.type(await screen.findByRole("textbox"), text);
  await user.keyboard("{Enter}");
}

async function tick(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

describe("formatElapsed", () => {
  const t = (key: string, vars?: Record<string, string | number>) =>
    ({ "code.chat.duration.s": "{n} s", "code.chat.duration.min": "{n} min", "code.chat.duration.h": "{n} h" }[key] ?? key)
      .replace("{n}", String(vars?.n));

  it("composes the two largest translated units", () => {
    expect(formatElapsed(7.9, t as never)).toBe("7 s");
    expect(formatElapsed(120, t as never)).toBe("2 min 0 s");
    expect(formatElapsed(3_900, t as never)).toBe("1 h 5 min");
    expect(formatElapsed(-3, t as never)).toBe("0 s");
  });
});

describe("live turn timing and context", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(streamCodeTurn).mockReset();
    localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    delete document.documentElement.dataset.motion;
  });

  it("times the running step from the server's start and stops on its measured duration", async () => {
    const turn = heldTurn(
      (h) => {
        h.onSession?.("s1", "t1", Date.now() / 1000);
        // The server started this step 125 s before this screen heard of it.
        h.onToolStart?.("run_shell", { command: "pytest" }, Date.now() / 1000 - 125);
      },
      (h) => {
        h.onTool?.({ name: "run_shell", arguments: { command: "pytest" }, ok: true, observation: "ok",
          duration_seconds: 127.4 });
        h.onDone?.(done({ elapsed_seconds: 130 }));
      },
    );
    mount();
    await send("time it");
    const step = await screen.findByTestId("step-elapsed");
    expect(step).toHaveTextContent("2 min 5 s");
    await tick(1_000);
    expect(screen.getByTestId("step-elapsed")).toHaveTextContent("2 min 6 s");
    await turn.release();
    expect(screen.queryByTestId("running-step")).toBeNull();
    expect(screen.getByTestId("step-duration")).toHaveTextContent("2 min 7 s");
    expect(screen.getByTestId("turn-elapsed")).toHaveTextContent("2 min 10 s");
    // Stopped: time passing no longer moves either number.
    await tick(5_000);
    expect(screen.getByTestId("step-duration")).toHaveTextContent("2 min 7 s");
    expect(screen.getByTestId("turn-elapsed")).toHaveTextContent("2 min 10 s");
  });

  it("runs the turn clock from the opening frame's server time", async () => {
    const turn = heldTurn(
      (h) => h.onSession?.("s1", "t1", Date.now() / 1000 - 65),
      (h) => h.onDone?.(done({ elapsed_seconds: 70 })),
    );
    mount();
    await send("time turn");
    expect(await screen.findByTestId("turn-elapsed")).toHaveTextContent("1 min 5 s");
    await tick(1_000);
    expect(screen.getByTestId("turn-elapsed")).toHaveTextContent("1 min 6 s");
    await turn.release();
    expect(screen.getByTestId("turn-elapsed")).toHaveTextContent("1 min 10 s");
  });

  it("with reduced motion the clock only changes its digits, at most once a second", async () => {
    document.documentElement.dataset.motion = "reduced";
    const turn = heldTurn((h) => h.onSession?.("s1", "t1", Date.now() / 1000), () => {});
    mount();
    await send("calm");
    const clock = await screen.findByTestId("turn-clock");
    expect(clock.outerHTML).not.toMatch(/animate-|transition/);
    expect(within(clock).getByTestId("turn-elapsed")).toHaveTextContent("0 s");
    await tick(400);
    expect(within(clock).getByTestId("turn-elapsed")).toHaveTextContent("0 s");
    await tick(700);
    expect(within(clock).getByTestId("turn-elapsed")).toHaveTextContent("1 s");
    await turn.release();
  });

  it("shows context as a percentage with a bar when the window is known, and hides it at the end", async () => {
    const turn = heldTurn(
      (h) => {
        h.onSession?.("s1", "t1", Date.now() / 1000);
        h.onContext?.(3_000, 10_000);
      },
      (h) => h.onDone?.(done()),
    );
    mount();
    await send("ctx");
    expect(await screen.findByText("Context 30%")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "30");
    await turn.release();
    expect(screen.queryByTestId("context-meter")).toBeNull();
  });

  it("shows plain tokens and no percentage when no window is known", async () => {
    const turn = heldTurn(
      (h) => {
        h.onSession?.("s1", "t1", Date.now() / 1000);
        h.onContext?.(3_000, null);
      },
      (h) => h.onDone?.(done()),
    );
    mount();
    await send("ctx");
    const meter = await screen.findByTestId("context-meter");
    expect(meter).toHaveTextContent("Context 3,000 tokens");
    expect(meter.textContent).not.toMatch(/%/);
    expect(screen.queryByRole("progressbar")).toBeNull();
    await turn.release();
  });
});
