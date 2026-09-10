import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApprovalCard, type ApprovalQuestionLike } from "@/components/code/ApprovalCard";
import { I18nProvider } from "@/lib/i18n";

const { answerApproval } = vi.hoisted(() => ({
  answerApproval: vi.fn(async (_id: string, _approved: boolean) => ({ ok: true })),
}));
vi.mock("@/lib/api", () => ({ answerApproval }));

const question = {
  id: "abc123",
  action: "",
  reason: "write_file is restricted after this run consumed untrusted content",
  wait_seconds: 300,
};

function mount(onAnswered: () => void = () => {}, q: ApprovalQuestionLike = question) {
  return render(
    <I18nProvider>
      <ApprovalCard question={q} onAnswered={onAnswered} />
    </I18nProvider>,
  );
}

describe("ApprovalCard — the other half of a pause", () => {
  it("shows the ledger's own reason and how long silence has before it refuses", () => {
    mount();
    expect(screen.getByRole("group", { name: /decision/i })).toBeInTheDocument();
    expect(screen.getByText(question.reason)).toBeInTheDocument();
    expect(screen.getByText(/300s/)).toBeInTheDocument();
  });

  it("a question listed after a reload has no wait to show, and shows none", () => {
    const { wait_seconds: _omit, ...listed } = question;
    mount(() => {}, listed);
    expect(screen.getByText(question.reason)).toBeInTheDocument();
    expect(screen.queryByText(/refuses after/i)).not.toBeInTheDocument();
  });

  it("approving answers the question by id with true, then clears the card", async () => {
    answerApproval.mockClear();
    const onAnswered = vi.fn();
    mount(onAnswered);
    await userEvent.click(screen.getByRole("button", { name: /allow this once/i }));
    await waitFor(() => expect(answerApproval).toHaveBeenCalledWith("abc123", true));
    expect(onAnswered).toHaveBeenCalledTimes(1);
  });

  it("refusing answers with false — a refusal is an answer, not the absence of one", async () => {
    answerApproval.mockClear();
    const onAnswered = vi.fn();
    mount(onAnswered);
    await userEvent.click(screen.getByRole("button", { name: /^refuse$/i }));
    await waitFor(() => expect(answerApproval).toHaveBeenCalledWith("abc123", false));
    expect(onAnswered).toHaveBeenCalledTimes(1);
  });

  it("a stale click still clears the card: a verdict on a resolved question has nowhere to go", async () => {
    answerApproval.mockResolvedValueOnce({ ok: false });
    const onAnswered = vi.fn();
    mount(onAnswered);
    await userEvent.click(screen.getByRole("button", { name: /allow this once/i }));
    await waitFor(() => expect(onAnswered).toHaveBeenCalledTimes(1));
  });
});

/**
 * The deadline was a sentence printed once, and a sentence printed once is only true once.
 *
 * `wait_seconds` was read at render and never read again, so the card went on saying "silence
 * refuses after 300s" — with two live-looking buttons under it — for as long as it was mounted, long
 * past the moment silence had actually refused. Pressing one then did nothing visible: the server
 * answered `ok: false` and the card cleared exactly as it does on a real answer, so the person left
 * believing they had allowed something that had been refused minutes earlier.
 *
 * Fake timers throughout, because a countdown tested with real ones is either a slow test or a flaky
 * one. `vi.useFakeTimers()` fakes `Date` as well as `setInterval`, which is what lets `asked_at` be
 * placed relative to a known now.
 */
describe("ApprovalCard — the countdown, and what zero means", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  /** Mount at a known instant so `asked_at` can be positioned against it. */
  function mountAt(now: number, q: ApprovalQuestionLike) {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    return mount(() => {}, q);
  }

  it("counts down instead of repeating the number it was born with", () => {
    mountAt(1_000_000_000_000, question);
    expect(screen.getByText(/300s/)).toBeInTheDocument();

    act(() => void vi.advanceTimersByTime(10_000));

    expect(screen.getByText(/290s/)).toBeInTheDocument();
    expect(screen.queryByText(/300s/)).not.toBeInTheDocument();
  });

  it("at zero it says silence refused, and stops offering an answer nobody can give", () => {
    mountAt(1_000_000_000_000, question);
    expect(screen.getByRole("button", { name: /allow this once/i })).toBeInTheDocument();

    act(() => void vi.advanceTimersByTime(300_000));

    // The whole point: the card explains the refusal rather than sitting on a stale question. The
    // turn's `✗ run_shell` is otherwise the only thing the person ever sees.
    expect(screen.getByText(/silence refused this/i)).toBeInTheDocument();
    expect(screen.queryByText(/refuses after/i)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /allow this once/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^refuse$/i })).not.toBeInTheDocument();
  });

  it("a question already half spent when the card mounts shows what is LEFT, not what it started with", () => {
    // The replay case: a dropped connection, `resumeCodeTurn` re-delivers the frame, and the
    // `asked_at` inside it is minutes old. Anchoring on arrival would restart the clock and promise
    // five minutes that do not exist.
    const now = 1_000_000_000_000;
    mountAt(now, { ...question, asked_at: now / 1000 - 290 });

    expect(screen.getByText(/10s/)).toBeInTheDocument();

    act(() => void vi.advanceTimersByTime(10_000));
    expect(screen.getByText(/silence refused this/i)).toBeInTheDocument();
  });

  it("a question replayed after its deadline reads as refused on sight", () => {
    const now = 1_000_000_000_000;
    mountAt(now, { ...question, asked_at: now / 1000 - 900 });

    expect(screen.getByText(/silence refused this/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /allow this once/i })).not.toBeInTheDocument();
  });

  it("does not believe a timestamp from the future — that is two clocks, not a live question", () => {
    // `asked_at` is the SERVER's epoch and this may be a remote Chimera. A question cannot be asked
    // later than now, so a negative elapsed proves the clocks differ; the card then counts from
    // arrival, which needs neither of them and is exact for a live frame.
    const now = 1_000_000_000_000;
    mountAt(now, { ...question, asked_at: now / 1000 + 600 });

    expect(screen.getByText(/300s/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /allow this once/i })).toBeInTheDocument();
  });

  it("a question with no deadline on the wire is never declared refused", () => {
    // `GET /api/approvals` carries no `wait_seconds`, so there is no deadline to count. Inventing
    // one would put "silence refused this" over a question that is still waiting for an answer.
    const { wait_seconds: _omit, ...listed } = question;
    mountAt(1_000_000_000_000, { ...listed, asked_at: 1_000_000_000 - 900 });

    act(() => void vi.advanceTimersByTime(600_000));

    expect(screen.queryByText(/silence refused this/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /allow this once/i })).toBeInTheDocument();
  });
});
