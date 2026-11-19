import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Conversation } from "@/components/code/Conversation";
import {
  getCodeSession, listShares, listWorks, streamSessionLive,
  type CodeTurnDone, type SessionLiveFrame,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const DONE: CodeTurnDone = {
  answer: "The label changed.", steps: 1, stopped_reason: "final", tool_names: [], model: "fake",
  prompt_tokens: 1, completion_tokens: 1, usd: 0, context_peak_tokens: 1, route_meta: null,
  close_loop_note: "payload",
  close_loop: { skipped_reason: "verification did not pass for this turn", skipped_code: "verification_failed" },
};
function mount() {
  return renderWithProviders(<Conversation
    workspace="/project" openFile={null} resumeSession="s"
    posture={{ reach: "workspace" as never, approval: "ask" as never }} profile={"balanced" as never}
    onHandOff={() => {}} onBatch={() => {}} onEdited={() => {}} busyElsewhere={false}
    controls={null} onOpenFile={() => {}}
  />);
}
describe("close-loop notes through the real conversation delivery paths", () => {
  afterEach(() => vi.useRealTimers());
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getCodeSession).mockReset();
    vi.mocked(streamSessionLive).mockReset().mockResolvedValue(null);
    vi.mocked(listShares).mockReset().mockResolvedValue({ shares: [] });
    vi.mocked(listWorks).mockReset().mockResolvedValue({ works: [] });
  });
  it.each(["en", "pt"])("renders the stored marked receipt once on replay in %s", async lang => {
    localStorage.setItem("chimera.lang", lang);
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s", workspace: "/project", exchanges: [
      { you: "Update the label", answer: DONE.answer, tools: [], edits: [], verified: null, done: DONE },
    ] });
    mount();
    expect(await screen.findByTestId("close-loop-note")).toHaveTextContent(
      lang === "pt" ? "a verificação deste turno não passou" : "verification did not pass for this turn",
    );
    expect(screen.getAllByTestId("close-loop-note")).toHaveLength(1);
    expect(screen.getByText(DONE.answer)).toBeInTheDocument();
  });
  it.each(["en", "pt"])("renders a live done frame once in %s, preserving streamed answer", async lang => {
    localStorage.setItem("chimera.lang", lang);
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s", workspace: "/project", exchanges: [], running_turn: {
      turn_id: "t", session_id: "s", workspace: "/project", message: "Update the label",
      started_at: 1, live_since: 0, transcript_saved: false,
    } });
    let deliver: ((frame: SessionLiveFrame) => void) | undefined;
    vi.mocked(streamSessionLive).mockImplementation((_id, _since, handler) => {
      deliver = handler;
      return new Promise<string | null>(() => {});
    });
    mount();
    await waitFor(() => expect(deliver).toBeDefined());
    const frame = (seq: number, event: string, payload: Record<string, unknown>): SessionLiveFrame => ({
      session_seq: seq, event, payload, turn_id: "t", author: "",
    });
    await act(async () => {
      deliver?.(frame(1, "turn_started", { message: "Update the label" }));
      deliver?.(frame(2, "token", { text: DONE.answer }));
      deliver?.(frame(3, "done", { ...DONE }));
    });
    expect(await screen.findByTestId("close-loop-note")).toHaveTextContent(
      lang === "pt" ? "a verificação deste turno não passou" : "verification did not pass for this turn",
    );
    expect(screen.getAllByTestId("close-loop-note")).toHaveLength(1);
    expect(screen.getByText(DONE.answer)).toBeInTheDocument();
  });
  it("polls only close-loop metadata through pending, provisional passed and a final block", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const pending = { ...DONE, close_loop: { turn_id: "original", ci: "pending", pr_url: "https://github.com/org/repo/pull/1" } };
    const session = (done: CodeTurnDone) => ({ id: "s", workspace: "/project", exchanges: [
      { you: "Update the label", answer: DONE.answer, tools: [], edits: [], verified: null, done },
    ] });
    vi.mocked(getCodeSession)
      .mockResolvedValueOnce(session(pending))
      .mockResolvedValueOnce(session({ ...pending, close_loop: { ...pending.close_loop, ci: "passed" } }))
      .mockResolvedValueOnce(session({ ...pending, answer: "must not replace the answer", close_loop: {
        ...pending.close_loop, ci: "passed", merge_blocked_code: "exceptions",
        merge_blocked_reason: "touches billing (invoice.py) — left open for the owner",
      } }));
    const view = mount();
    expect(await screen.findByTestId("close-loop-note")).toHaveTextContent("CI pending");
    const draft = screen.getByPlaceholderText("Ask about this code, or say what to change…");
    fireEvent.change(draft, { target: { value: "keep this draft" } });
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent("CI passed");
    expect(getCodeSession).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent("not merged");
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent("invoice.py");
    expect(screen.getAllByTestId("close-loop-note")).toHaveLength(1);
    expect(screen.getByText(DONE.answer)).toBeInTheDocument();
    expect(screen.queryByText("must not replace the answer")).not.toBeInTheDocument();
    expect(draft).toHaveValue("keep this draft");
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(getCodeSession).toHaveBeenCalledTimes(3);
    view.unmount();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(getCodeSession).toHaveBeenCalledTimes(3);
  });
  it("stops polling a pending receipt whose watcher reports a terminal error", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const done = { ...DONE, close_loop: { turn_id: "original", ci: "pending" } };
    vi.mocked(getCodeSession).mockResolvedValueOnce({ id: "s", workspace: "/project", exchanges: [
      { you: "Update the label", answer: DONE.answer, tools: [], edits: [], verified: null, done },
    ] }).mockResolvedValue({ id: "s", workspace: "/project", exchanges: [
      { you: "Update the label", answer: DONE.answer, tools: [], edits: [], verified: null,
        done: { ...done, close_loop: { ...done.close_loop, error: "watch budget finished" } } },
    ] });
    mount();
    await screen.findByTestId("close-loop-note");
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(screen.getByTestId("close-loop-note")).toHaveTextContent("watch budget finished");
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(getCodeSession).toHaveBeenCalledTimes(2);
  });

  it("cleans up a still-pending poll when the conversation closes", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const done = { ...DONE, close_loop: { turn_id: "original", ci: "pending" } };
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s", workspace: "/project", exchanges: [
      { you: "Update the label", answer: DONE.answer, tools: [], edits: [], verified: null, done },
    ] });
    const view = mount();
    await screen.findByTestId("close-loop-note");
    view.unmount();
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(getCodeSession).toHaveBeenCalledTimes(1);
  });
  it("does not poll legacy receipts without the presentation marker", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const done = { ...DONE, close_loop_note: undefined, close_loop: { turn_id: "original", ci: "pending" } };
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s", workspace: "/project", exchanges: [
      { you: "Update the label", answer: DONE.answer, tools: [], edits: [], verified: null, done },
    ] });
    mount();
    await screen.findByText(DONE.answer);
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(getCodeSession).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("close-loop-note")).not.toBeInTheDocument();
  });
  it("leaves an active streamed turn and its draft alone while an older loop is pending", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    const done = { ...DONE, close_loop: { turn_id: "original", ci: "pending" } };
    vi.mocked(getCodeSession).mockResolvedValue({ id: "s", workspace: "/project", exchanges: [
      { you: "Update the label", answer: DONE.answer, tools: [], edits: [], verified: null, done },
    ], running_turn: { turn_id: "next", session_id: "s", workspace: "/project", message: "Explain the label",
      started_at: 1, live_since: 0, transcript_saved: false } });
    let deliver: ((frame: SessionLiveFrame) => void) | undefined;
    vi.mocked(streamSessionLive).mockImplementation((_id, _since, handler) => {
      deliver = handler;
      return new Promise<string | null>(() => {});
    });
    mount();
    await waitFor(() => expect(deliver).toBeDefined());
    await act(async () => {
      deliver?.({ session_seq: 1, event: "turn_started", turn_id: "next", author: "", payload: { message: "Explain the label" } });
      deliver?.({ session_seq: 2, event: "token", turn_id: "next", author: "", payload: { text: "A new streamed answer" } });
    });
    await screen.findByText("A new streamed answer");
    await act(() => vi.advanceTimersByTimeAsync(120_000));
    expect(getCodeSession).toHaveBeenCalledTimes(1);
    expect(screen.getByText("A new streamed answer")).toBeInTheDocument();
    expect(screen.getByText(DONE.answer)).toBeInTheDocument();
  });

});
