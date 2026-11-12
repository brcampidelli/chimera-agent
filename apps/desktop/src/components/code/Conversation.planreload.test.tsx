import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PlanCard } from "@/components/code/side/PlanCard";
import { Conversation } from "@/components/code/Conversation";
import { answerApproval, getCodeSession, streamSessionLive, type SessionLiveFrame } from "@/lib/api";
import { ConversationSideProvider } from "@/lib/conversation-side";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A reopened conversation whose last turn is parked on its plan's approval. Nothing here is streamed
 * by this screen: the server is the only source. It holds the conversation (`getCodeSession`, with the
 * running turn named in `running_turn`), and the turn's frames on the conversation's live stream, which
 * is what a fresh mount reads back. The Plan panel must come back from those, with the same question
 * id the card would answer.
 */
const STEPS = ["Ler src/a.ts", "Editar src/a.ts"];
const QUESTION_ID = "q-plan-1";

function frame(session_seq: number, event: string, payload: Record<string, unknown>): SessionLiveFrame {
  return { session_seq, event, turn_id: "t1", author: "", payload };
}

/** The frames a turn sends while it waits on its plan, in the order the server emits them. */
const TURN_FRAMES: SessionLiveFrame[] = [
  frame(1, "turn_started", { message: "arruma o cabeçalho", author: "", attachment_count: 0, started_at: 1 }),
  frame(2, "plan", { steps: STEPS, raw: STEPS.map((s, i) => `${i + 1}. ${s}`).join("\n"), approval_id: QUESTION_ID }),
  frame(3, "approval", {
    id: QUESTION_ID,
    action: STEPS.map((s, i) => `${i + 1}. ${s}`).join("\n"),
    reason: "",
    host: null,
    irreversible: false,
    allow_scope: "",
    preview: [],
    preview_status: "",
    asked_at: 1,
    decision: "review",
    p: null,
    band: null,
    decider_model: null,
    decision_id: null,
    wait_seconds: 900,
  }),
];

function mountReopened() {
  renderWithProviders(
    <ConversationSideProvider value={{ workspace: "/proj", sessionId: "s1" }}>
      <Conversation
        workspace="/proj"
        resumeSession="s1"
        openFile={null}
        posture={{ reach: "workspace" as never, approval: "ask" as never }}
        profile={"balanced" as never}
        onHandOff={() => {}}
        onBatch={() => {}}
        onEdited={() => {}}
        busyElsewhere={false}
        controls={null}
        onOpenFile={() => {}}
      />
      <PlanCard />
    </ConversationSideProvider>,
  );
}

describe("the Plan panel after a reload", () => {
  beforeEach(() => {
    vi.mocked(answerApproval).mockReset().mockResolvedValue({ ok: true, outcome: "approved" } as never);
    vi.mocked(getCodeSession).mockResolvedValue({
      id: "s1",
      workspace: "/proj",
      exchanges: [],
      running_turn: {
        turn_id: "t1",
        session_id: "s1",
        workspace: "/proj",
        message: "arruma o cabeçalho",
        started_at: 1,
        live_since: 0,
        transcript_saved: false,
      },
    } as never);
    vi.mocked(streamSessionLive).mockImplementation(async (_sid, _since, onFrame) => {
      for (const f of TURN_FRAMES) onFrame(f);
      return null;
    });
  });

  it("shows the plan and its approval answers when a new mount reads a turn waiting on its plan", async () => {
    mountReopened();

    expect(await screen.findByRole("button", { name: "Approve plan" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask for changes" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(STEPS);
  });

  it("approves the plan through the question id the server gave the plan", async () => {
    mountReopened();

    await userEvent.click(await screen.findByRole("button", { name: "Approve plan" }));

    expect(answerApproval).toHaveBeenCalledWith(QUESTION_ID, true);
    await act(async () => {});
  });

  it("does not hold the plan on a question just because its text matches the steps", async () => {
    // The pairing is the id. A plan frame without one is shown, but offers no answers: the text of the
    // question is no longer evidence that it is the question this plan raised.
    vi.mocked(streamSessionLive).mockImplementation(async (_sid, _since, onFrame) => {
      onFrame(TURN_FRAMES[0]);
      onFrame(frame(2, "plan", { steps: STEPS, raw: "" }));
      onFrame(TURN_FRAMES[2]);
      return null;
    });
    mountReopened();

    // The premise of this case was wrong: the question on the card is not this plan's question, but the
    // turn is still waiting, so the panel must not say the plan was approved. It says where to answer.
    expect(await screen.findByText("Waiting for your answer on the approval card")).toBeInTheDocument();
    expect(screen.queryByText("Approved")).toBeNull();
    expect(screen.queryByRole("button", { name: "Approve plan" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask for changes" })).toBeNull();
  });

  it("does not hold the plan on a question with a different id", async () => {
    vi.mocked(streamSessionLive).mockImplementation(async (_sid, _since, onFrame) => {
      onFrame(TURN_FRAMES[0]);
      onFrame(frame(2, "plan", { steps: STEPS, raw: "", approval_id: "q-other" }));
      onFrame(TURN_FRAMES[2]);
      return null;
    });
    mountReopened();

    expect(await screen.findByText("Waiting for your answer on the approval card")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve plan" })).toBeNull();
  });
});
