import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import {
  getCodeSession,
  listShares,
  listWorks,
  stopCodeTurn,
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
};

describe("a conversation reopened while its turn is running", () => {
  beforeEach(() => {
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
