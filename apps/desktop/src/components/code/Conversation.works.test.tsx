import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import {
  getCodeSession,
  listShares,
  listWorks,
  stopWork,
  streamCodeTurn,
  streamSessionLive,
  undoWork,
  type CodeTurnHandlers,
  type SessionLiveFrame,
} from "@/lib/api";
import type { WorkInfo } from "@/lib/types";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * Background works on the conversation screen.
 *
 * A spoken request for work comes back from the turn's stream as `work_started` + a `done` that
 * ran nothing: the exchange says the work started, the composer is free again, the work's card is
 * on the panel, and the live stream opens so the card can move. A `work_state` frame moves it —
 * to done with its answer, to stopped, to failed. Stop and Undo call the two routes and take the
 * record they return. A reopened conversation lists its works from the server.
 */
function mount(resume?: string) {
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

function work(over: Partial<WorkInfo> = {}): WorkInfo {
  return {
    id: "w1",
    parent: "s1",
    session_id: "ws1",
    turn_id: "wt1",
    workspace: "/proj",
    title: "corrija o login",
    model: "openrouter/deepseek/deepseek-r1",
    state: "running",
    created_at: 1,
    started_at: 1,
    finished_at: null,
    edits: [],
    tools: 0,
    steps: 0,
    usd: null,
    answer: "",
    error: "",
    verified: "",
    can_undo: false,
    reported: false,
    author: "",
    number: 1,
    ...over,
  };
}

function live(seq: number, event: string, payload: Record<string, unknown>): SessionLiveFrame {
  return { session_seq: seq, event, turn_id: "", author: "", payload };
}

/** Capture the live window's frame handler, keeping the stream "open" until the test ends. */
function openLiveWindow() {
  let onFrame: ((f: SessionLiveFrame) => void) | null = null;
  vi.mocked(streamSessionLive).mockImplementation((_sid, _since, handler) => {
    onFrame = handler;
    return new Promise<string | null>(() => {});
  });
  return () => onFrame;
}

describe("background works on the conversation", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset();
    vi.mocked(streamSessionLive).mockReset().mockResolvedValue(null);
    vi.mocked(listShares).mockReset().mockResolvedValue({ shares: [] });
    vi.mocked(listWorks).mockReset().mockResolvedValue({ works: [] });
    vi.mocked(stopWork).mockReset();
    vi.mocked(undoWork).mockReset();
    vi.mocked(getCodeSession).mockReset().mockResolvedValue({ id: "s1", workspace: "/w", exchanges: [] });
    localStorage.clear();
  });

  it("turns a spoken request into a work card, frees the composer, and follows the work live", async () => {
    const handler = openLiveWindow();
    vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
      h.onSession?.("s1", "wt1");
      h.onWorkStarted?.(work());
      h.onDone?.({
        answer: "", steps: 0, stopped_reason: "work_started", tool_names: [], model: "",
        prompt_tokens: 0, completion_tokens: 0, usd: null, tainted: false, memory_facts_used: 0,
        memory_layer: "", fused: false,
      } as never);
    });
    mount("s1");
    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "corrija o login{Enter}");

    // The exchange says what happened to the request; nothing ran here.
    await waitFor(() => expect(screen.getByText(/started background work 1/i)).toBeInTheDocument());
    expect(screen.queryByText(/price unknown|passos|steps/i)).not.toBeInTheDocument();
    // The card, running; the composer is usable again while it runs.
    const card = await screen.findByTestId("work-card");
    expect(card).toHaveAttribute("data-state", "running");
    expect(screen.getByTestId("work-state")).toHaveTextContent(/running/i);
    // The composer is free: a second message goes out at once rather than into the queue.
    await userEvent.type(screen.getByRole("textbox"), "e o logout?{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    // The live window is open because a work is active — no share needed.
    await waitFor(() => expect(handler()).not.toBeNull());

    // The work ends: the card says so and shows the gist; Undo appears when there is an offer.
    act(() => {
      handler()!(live(5, "work_progress", { work: work({ tools: 3, edits: ["login.py"] }) }));
      handler()!(live(6, "work_state", { work: work({ state: "done", finished_at: 9, answer: "Fixed the login. Two files.", edits: ["login.py"], can_undo: true, verified: "passed" }) }));
    });
    expect(screen.getByTestId("work-card")).toHaveAttribute("data-state", "done");
    expect(screen.getByTestId("work-answer")).toHaveTextContent("Fixed the login. Two files.");
    expect(screen.getByText(/1 file\(s\) edited/i)).toBeInTheDocument();
    expect(screen.queryByTestId("work-stop")).not.toBeInTheDocument();

    vi.mocked(undoWork).mockResolvedValue({ ok: true, reason: "", work: work({ state: "undone", finished_at: 9, can_undo: false }) });
    await userEvent.click(screen.getByTestId("work-undo"));
    await waitFor(() => expect(undoWork).toHaveBeenCalledWith("w1"));
    await waitFor(() => expect(screen.getByTestId("work-card")).toHaveAttribute("data-state", "undone"));
  });

  it("stops a running work from its card and takes the state the server returns", async () => {
    vi.mocked(listWorks).mockResolvedValue({ works: [work()] });
    vi.mocked(stopWork).mockResolvedValue({ ok: true, reason: "", work: work({ state: "stopped", finished_at: 4 }) });
    mount("s1");
    const stop = await screen.findByTestId("work-stop");
    await userEvent.click(stop);
    await waitFor(() => expect(stopWork).toHaveBeenCalledWith("w1"));
    await waitFor(() => expect(screen.getByTestId("work-card")).toHaveAttribute("data-state", "stopped"));
    expect(screen.queryByTestId("work-undo")).not.toBeInTheDocument(); // nothing to undo without an offer
  });

  it("lists a reopened conversation's works, failed ones with their error, and draws nothing without any", async () => {
    vi.mocked(listWorks).mockResolvedValue({
      works: [
        work({ id: "a", number: 1, state: "done", finished_at: 3, answer: "Done." }),
        work({ id: "b", number: 2, state: "failed", finished_at: 3, error: "the app stopped while this work was running" }),
      ],
    });
    mount("s1");
    const cards = await screen.findAllByTestId("work-card");
    expect(cards).toHaveLength(2);
    expect(screen.getByTestId("work-error")).toHaveTextContent(/the app stopped/i);
    // No work active: the live window stays closed.
    await new Promise((r) => setTimeout(r, 20));
    expect(streamSessionLive).not.toHaveBeenCalled();
  });
});
