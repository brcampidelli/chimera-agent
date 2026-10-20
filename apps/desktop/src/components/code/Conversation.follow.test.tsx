import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation, SEND_AND_STOP_FALLBACK_MS } from "@/components/code/Conversation";
import {
  getCodeSession,
  listShares,
  listWorks,
  sendCodeTurnGuidance,
  stopCodeTurn,
  type CodeTurnHandlers,
  type CodeTurnInput,
  streamCodeTurn,
  streamSessionLive,
  type RunningTurn,
  type SessionLiveFrame,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * Coming back to a conversation whose turn is still running.
 *
 * Measured live on 2026-09-29: a turn keeps running on the server when the screen that started it
 * goes away, but the conversation is stored when the agent finishes, so a screen that reopened it
 * mid-turn read an empty file and looked idle. The server now names the running turn; the screen
 * follows it through the conversation's live stream from the frame before its opening one, shows
 * it as working (so a message typed meanwhile waits behind it), and comes back when it ends.
 *
 * The stretch after the agent finishes is the subtle one: the transcript is already stored and the
 * turn is still verifying, so the stored exchange and the replay describe the same turn, and the
 * screen must draw it once.
 */
function mount(resume: string) {
  return renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      resumeSession={resume}
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

const TURN: RunningTurn = {
  turn_id: "t1",
  session_id: "s1",
  workspace: "/proj",
  message: "audit the gateway",
  started_at: 1,
  live_since: 7,
  transcript_saved: false,
};

function frame(seq: number, event: string, payload: Record<string, unknown> = {}): SessionLiveFrame {
  return { session_seq: seq, event, turn_id: "t1", author: "", payload };
}

/** Hold the live window open and hand back what it was asked and how to speak into it. */
function openLiveWindow() {
  const seen: { since: number; signal?: AbortSignal } = { since: -1 };
  let onFrame: ((f: SessionLiveFrame) => void) | null = null;
  vi.mocked(streamSessionLive).mockImplementation((_sid, since, handler, signal) => {
    seen.since = since;
    seen.signal = signal;
    onFrame = handler;
    return new Promise<string | null>(() => {});
  });
  return { seen, send: (f: SessionLiveFrame) => act(() => onFrame?.(f)) };
}

const DONE = {
  answer: "the gateway routes by model",
  steps: 3,
  stopped_reason: "final",
  tool_names: [],
  model: "openrouter/x/y",
  prompt_tokens: 10,
  completion_tokens: 5,
  usd: 0.001,
  tainted: false,
  memory_facts_used: 0,
  memory_layer: "",
  fused: false,
  context_peak_tokens: 0,
  route_meta: null,
};

describe("a conversation reopened while its turn is running", () => {
  beforeEach(() => {
    vi.useRealTimers();
    vi.mocked(streamCodeTurn).mockReset();
    vi.mocked(streamSessionLive).mockReset().mockResolvedValue(null);
    vi.mocked(listShares).mockReset().mockResolvedValue({ shares: [] });
    vi.mocked(listWorks).mockReset().mockResolvedValue({ works: [] });
    vi.mocked(getCodeSession).mockReset();
    localStorage.clear();
  });

  it("follows the running turn from the frame before its opening one, and shows it working", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    mount("s1");

    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));
    await live.send(frame(9, "token", { text: "reading the router" }));

    expect(await screen.findByText("audit the gateway")).toBeInTheDocument();
    expect(await screen.findByText(/reading the router/)).toBeInTheDocument();
    // Working: the button that stops is on screen, the one that sends is not.
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^send$/i })).not.toBeInTheDocument();
  });

  it("sends Enter as guidance and shows it until the guidance frame confirms delivery", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));
    vi.mocked(sendCodeTurnGuidance).mockClear();

    const box = await screen.findByPlaceholderText("Ask about this code, or say what to change…");
    await userEvent.type(box, "focus on auth{Enter}");

    await waitFor(() => expect(sendCodeTurnGuidance).toHaveBeenCalledWith("t1", "focus on auth"));
    expect(await screen.findByTestId("pending-guidance")).toHaveTextContent("Sending guidance: focus on auth");
    await live.send(frame(9, "guidance", { text: "focus on auth" }));
    expect(await screen.findByTestId("delivered-guidance")).toHaveTextContent("Guidance delivered: focus on auth");
    expect(screen.queryByTestId("pending-guidance")).not.toBeInTheDocument();
  });

  it("moves guidance still pending at turn end into the editable queue", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));
    await userEvent.type(await screen.findByPlaceholderText("Ask about this code, or say what to change…"), "keep this instruction{Enter}");
    expect(await screen.findByTestId("pending-guidance")).toHaveTextContent("keep this instruction");

    await live.send(frame(9, "done", DONE));

    expect(await screen.findByRole("textbox", { name: "Edit queued message 1" })).toHaveValue("keep this instruction");
    expect(screen.queryByTestId("pending-guidance")).not.toBeInTheDocument();
  });

  it("sends guidance again as a new turn when a 409 arrives after the old turn ended", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    const requests: CodeTurnInput[] = [];
    vi.mocked(streamCodeTurn).mockImplementation(async (request, handlers) => {
      requests.push(request);
      handlers.onSession?.("s1", `new-turn-${requests.length}`);
      handlers.onDone?.(DONE);
    });
    let rejectGuidance: ((error: Error) => void) | undefined;
    vi.mocked(sendCodeTurnGuidance).mockImplementation(() => new Promise((_, reject) => {
      rejectGuidance = reject;
    }));
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));
    await userEvent.type(await screen.findByPlaceholderText("Ask about this code, or say what to change…"), "preserve this{Enter}");
    await live.send(frame(9, "done", DONE));
    rejectGuidance?.(Object.assign(new Error("not steerable"), { status: 409 }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect((requests[0] as { message: string }).message).toBe("preserve this");
    expect(screen.queryByRole("textbox", { name: "Edit queued message 1" })).not.toBeInTheDocument();
  });

  it("puts rejected network guidance back in the draft with a real line break after the turn ends", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    let rejectGuidance: ((error: Error) => void) | undefined;
    vi.mocked(sendCodeTurnGuidance).mockImplementation(() => new Promise((_, reject) => {
      rejectGuidance = reject;
    }));
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));
    const box = await screen.findByPlaceholderText("Ask about this code, or say what to change…");
    await userEvent.type(box, "preserve this{Enter}");
    await live.send(frame(9, "done", DONE));
    await userEvent.type(box, "already in the draft");

    await act(async () => rejectGuidance?.(new Error("network unavailable")));

    await waitFor(() => expect(box).toHaveValue("preserve this\nalready in the draft"));
  });

  it("returns rejected guidance to the editable local queue", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));
    vi.mocked(sendCodeTurnGuidance).mockRejectedValueOnce(Object.assign(new Error("not steerable"), { status: 409 }));

    await userEvent.type(await screen.findByPlaceholderText("Ask about this code, or say what to change…"), "preserve this{Enter}");

    const queued = await screen.findByRole("textbox", { name: "Edit queued message 1" });
    expect(queued).toHaveValue("preserve this");
    await userEvent.clear(queued);
    await userEvent.type(queued, "corrected");
    expect(await screen.findByRole("textbox", { name: "Edit queued message 1" })).toHaveValue("corrected");
  });

  it("comes back when the turn ends: the answer lands and the composer is free again", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));

    await live.send(frame(9, "done", DONE));

    expect(await screen.findByText(/the gateway routes by model/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /^send$/i })).toBeInTheDocument();
  });

  it("does not draw the exchange twice when the transcript is already stored (the turn is verifying)", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1",
      workspace: "/proj",
      exchanges: [
        {
          you: "audit the gateway", answer: "the gateway routes by model", tools: [], edits: [],
          done: null, verified: null,
        },
      ],
      running_turn: { ...TURN, transcript_saved: true },
    });
    const live = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));

    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));
    await live.send(frame(9, "token", { text: "the gateway routes by model" }));

    await waitFor(() => expect(screen.getAllByText("audit the gateway")).toHaveLength(1));
  });

  it("keeps the stored exchange that is NOT the running turn's, when the transcript is not stored yet", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1",
      workspace: "/proj",
      exchanges: [
        { you: "an earlier question", answer: "an earlier answer", tools: [], edits: [], done: null, verified: null },
      ],
      running_turn: TURN,
    });
    const live = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));

    expect(await screen.findByText("an earlier question")).toBeInTheDocument();
    expect(await screen.findByText("audit the gateway")).toBeInTheDocument();
  });

  // This test used to be called "stopping only stops watching", and it was true: Stop aborted the
  // screen's request and the turn went on working and spending on the server. Stop now ends the
  // turn itself (POST /api/code/turns/{id}/stop), so the claim is rewritten, not dropped: the view
  // still lets go, AND the server is asked to stop the turn it was following.
  it("stopping ends the followed turn on the server, and the live window closes", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));

    await userEvent.click(await screen.findByRole("button", { name: /stop/i }));

    await waitFor(() => expect(live.seen.signal?.aborted).toBe(true));
    expect(stopCodeTurn).toHaveBeenCalledWith("t1");
    expect(screen.getByRole("button", { name: /^send$/i })).toBeInTheDocument();
  });

  it("send and stop carries the draft into the next turn and stops this one", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s1", workspace: "/proj", exchanges: [] });
    const requests: CodeTurnInput[] = [];
    const firstTurn = { finish: undefined as (() => void) | undefined };
    vi.mocked(streamCodeTurn).mockImplementation(async (request: CodeTurnInput, h: CodeTurnHandlers) => {
      requests.push(request);
      h.onSession?.("s1", `own-turn-${requests.length}`);
      if (requests.length > 1) h.onDone?.(DONE);
      else {
        await new Promise<void>((resolve) => { firstTurn.finish = resolve; });
        h.onDone?.({ ...DONE, stopped_reason: "cancelled" });
      }
    });
    mount("s1");
    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "first request{Enter}");
    await userEvent.type(box, "next instruction");

    await userEvent.click(await screen.findByRole("button", { name: /send and stop/i }));

    expect(stopCodeTurn).toHaveBeenCalledWith("own-turn-1");
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
    firstTurn.finish?.();
    await waitFor(() => expect(requests).toHaveLength(2));
    expect((requests[1] as { message: string }).message).toBe("next instruction");
    const lateHandlers = vi.mocked(streamCodeTurn).mock.calls[0]?.[1];
    lateHandlers?.onToken?.("stale old text");
    expect(screen.queryByText(/stale old text/)).not.toBeInTheDocument();
    expect(screen.getByText(/the gateway routes by model/)).toBeInTheDocument();
  });

  it("send-and-stop releases the next turn if stop fails and the old stream never closes", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s1", workspace: "/proj", exchanges: [] });
    vi.mocked(stopCodeTurn).mockImplementation(() => new Promise((_, reject) => {
      window.setTimeout(() => reject(new Error("stop request failed")), 0);
    }));
    const seen: { handlers: CodeTurnHandlers; signal?: AbortSignal }[] = [];
    vi.mocked(streamCodeTurn).mockImplementation(async (_request, handlers, signal) => {
      seen.push({ handlers, signal });
      handlers.onSession?.("s1", `own-turn-${seen.length}`);
      if (seen.length > 1) return;
      await new Promise<void>(() => {});
    });
    mount("s1");
    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "first request{Enter}");
    await userEvent.type(box, "next instruction");
    const button = await screen.findByRole("button", { name: /send and stop/i });
    fireEvent.click(button);
    expect(screen.getByRole("textbox")).toHaveValue("");
    await waitFor(() => expect(seen).toHaveLength(2), { timeout: SEND_AND_STOP_FALLBACK_MS + 2_000 });
    expect(seen[0]?.signal?.aborted).toBe(true);
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
    act(() => seen[0]?.handlers.onError?.("late error from old turn"));
    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("");
  }, SEND_AND_STOP_FALLBACK_MS + 8_000);

  it("stopping a turn this screen started ends it on the server too", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s1", workspace: "/proj", exchanges: [] });
    vi.mocked(stopCodeTurn).mockClear();
    vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: { onSession?: (id: string, turnId?: string) => void }) => {
      h.onSession?.("s1", "own-turn");
      await new Promise<void>(() => {});
    });
    mount("s1");
    await userEvent.type(await screen.findByRole("textbox"), "refactor it{Enter}");

    await userEvent.click(await screen.findByRole("button", { name: /stop/i }));

    expect(stopCodeTurn).toHaveBeenCalledWith("own-turn");
  });

  it("leaves another turn's closing frame alone", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: TURN,
    });
    const live = openLiveWindow();
    mount("s1");
    await waitFor(() => expect(live.seen.since).toBe(7));
    await live.send(frame(8, "turn_started", { message: "audit the gateway" }));

    await live.send({ ...frame(9, "done", DONE), turn_id: "someone-elses" });

    expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument();
  });

  it("does not open the live window for a conversation nobody is working in", async () => {
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1", workspace: "/proj", exchanges: [], running_turn: null,
    });
    mount("s1");

    await screen.findByRole("textbox");
    await waitFor(() => expect(getCodeSession).toHaveBeenCalled());

    expect(streamSessionLive).not.toHaveBeenCalled();
  });
});
