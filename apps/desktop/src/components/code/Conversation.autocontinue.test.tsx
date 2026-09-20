import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { streamCodeTurn, type CodeTurnHandlers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * "Continue sozinho": a turn that stopped at the step ceiling is continued automatically.
 *
 * The feature exists because `max_steps` is the one stop that means "the task was going fine and
 * ran out of room" — the model was working, the ceiling cut it, and the work is incomplete by
 * arithmetic rather than by failure. Every other reason is a verdict, and continuing past a verdict
 * spends the user's money against a decision they already made. These tests pin both halves: the
 * continuation happens, and it happens ONLY for `max_steps`, ONLY up to three times, and never
 * after the user pressed Stop.
 */
function done(reason: string) {
  return {
    answer: "",
    steps: 1,
    stopped_reason: reason,
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
}

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

/** Every turn ends at the step ceiling, so the only thing that can stop the loop is the cap.
 *
 *  `onDone` is deferred by a tick on purpose. A real turn ends asynchronously; calling it
 *  synchronously inside `send` would batch `setBusy(true)` and `setBusy(false)` into one render,
 *  `busy` would never be observed as true, and the `[busy]` effect that carries the continuation
 *  would never fire — a test that fails for a reason the app does not have. */
function alwaysMaxSteps() {
  vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
    await new Promise((r) => setTimeout(r, 0));
    h.onDone?.(done("max_steps"));
  });
}

describe("auto-continue at the step ceiling", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset();
    localStorage.clear();
  });

  it("continues a turn stopped at the step limit, but never more than three times", async () => {
    // The whole point of the cap: a task that needs more room gets it, a task that will never end
    // does not get to spend forever. Four stops in a row must produce exactly one user turn plus
    // three automatic ones — the fourth stop is where the automation lets go.
    localStorage.setItem("chimera.autoContinue", "1");
    alwaysMaxSteps();
    mount();

    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "termina a migração{Enter}");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(4));
    // Give the machinery a beat to prove it does NOT go to five.
    await new Promise((r) => setTimeout(r, 20));
    expect(streamCodeTurn).toHaveBeenCalledTimes(4);
  });

  it("does not continue a turn that stopped for any reason other than the step limit", async () => {
    // `tool_loop` is the model repeating itself: continuing would repeat it again, on the user's
    // money. One call, and no second one, is the whole assertion.
    localStorage.setItem("chimera.autoContinue", "1");
    vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
      await new Promise((r) => setTimeout(r, 0));
      h.onDone?.(done("tool_loop"));
    });
    mount();

    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "conserta o loop{Enter}");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(streamCodeTurn).toHaveBeenCalledTimes(1);
  });

  it("stays off until the user turns it on", async () => {
    // Off by default, same reason as the notifications toggle: a chat that keeps sending turns on
    // its own is a chat that keeps spending on its own.
    alwaysMaxSteps();
    mount();

    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "só uma vez{Enter}");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(streamCodeTurn).toHaveBeenCalledTimes(1);
  });

  it("cancels the sequence when the user presses Stop", async () => {
    // A stop the user pressed is a stop the user decided. The first turn ends at the ceiling and
    // arms a continuation; the second turn is left running so the Stop button is on screen. After
    // the click, nothing else may be sent — the button must not stop one turn and let the
    // automation start another.
    localStorage.setItem("chimera.autoContinue", "1");
    let calls = 0;
    vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
      calls += 1;
      if (calls === 1) {
        await new Promise((r) => setTimeout(r, 0));
        h.onDone?.(done("max_steps"));
        return;
      }
      // The second turn never finishes on its own: it is the one the user stops.
      return new Promise<void>(() => {});
    });
    mount();

    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "para quando eu mandar{Enter}");

    // The automatic continuation is in flight, so the Stop button is up.
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    await userEvent.click(screen.getByRole("button", { name: /stop|parar/i }));

    await new Promise((r) => setTimeout(r, 20));
    expect(streamCodeTurn).toHaveBeenCalledTimes(2);
  });
});