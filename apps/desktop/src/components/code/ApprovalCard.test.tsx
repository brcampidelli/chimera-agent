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

  it("refuse is the FIRST button, so a dialog's focus trap lands on the safe answer", () => {
    // The fail-safe the other two surfaces already declare: the TUI focuses `#ask-no` so that
    // "Enter without reading" refuses, and the REPL prints `[y/N]`. This card shipped the inverse —
    // allow first and styled primary — and `PendingApprovals` mounts it inside a Radix dialog,
    // whose trap focuses the first focusable element. Order is the whole mechanism here, so the
    // order is what gets pinned; a future tidy that reorders the buttons has to fail this.
    mount();
    const buttons = screen.getAllByRole("button");
    expect(buttons[0]).toHaveAccessibleName(/^refuse$/i);
    expect(buttons[1]).toHaveAccessibleName(/allow this once/i);
  });

  it("refuse is drawn as the primary action and allow as the quiet one", () => {
    // Order settles the focus trap; weight settles what the eye lands on, and the two are separate
    // claims. This card shipped with ALLOW as the gradient pill and REFUSE as the outline, so the
    // loud button was the dangerous one. A screenshot would show this and prove nothing later — the
    // class is what a future edit has to get past.
    mount();
    const [refuse, allow] = screen.getAllByRole("button");
    expect(refuse.className).toContain("bg-accent-grad");
    expect(allow.className).toContain("border-border");
    expect(allow.className).not.toContain("bg-accent-grad");
  });

  it("shows the level the backend sorted the queue by", () => {
    // `decision` has always been on the wire and in the CLI table; this card was the one surface
    // that dropped it, so the person answering got a risk-ordered queue with the risk removed.
    mount(() => {}, { ...question, decision: "review" });
    expect(screen.getByText(/needs review/i)).toBeInTheDocument();
  });

  it("a level the interface has no word for is shown as nothing, never raw and never guessed", () => {
    mount(() => {}, { ...question, decision: "escalated" });
    expect(screen.queryByText(/escalated/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/needs review/i)).not.toBeInTheDocument();
    expect(screen.getByText(question.reason)).toBeInTheDocument();
  });

  it("a question with no level still renders — the chip is the only thing missing", () => {
    mount();
    expect(screen.getByText(question.reason)).toBeInTheDocument();
    expect(screen.queryByText(/needs review/i)).not.toBeInTheDocument();
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
 * The number that raised the question, and what it was read against.
 *
 * Study 20 §2.6: the card showed a reason and nothing else, so the answer it collected could not be
 * joined to the probability that asked — and the probability is the only thing that turns an answer
 * into a LABEL for a map refitted on the deployment's own data, where today the map comes from 55
 * bench items.
 *
 * Three things are held here, and each fails a different way: the number is shown when there is one,
 * it is NOT invented when there is not, and it is formatted the way the reader's language formats a
 * decimal — `0.80` in English and `0,80` in Portuguese, because this line sits inside a translated
 * sentence and a probability read as a count is the misread `useNum` exists to prevent.
 */
describe("ApprovalCard — the number that asked", () => {
  it("shows p, the band and the build that answered, on one line", () => {
    mount(() => {}, { ...question, decision: "review", p: 0.8, band: "review", decider_model: "qwen3:4b@Q4_K_M" });
    expect(screen.getByText(/p=0\.80/)).toBeInTheDocument();
    expect(screen.getByText(/band REVIEW/)).toBeInTheDocument();
    expect(screen.getByText(/qwen3:4b@Q4_K_M/)).toBeInTheDocument();
  });

  it("a question with no number shows no number — 0.00 would be an invented ALLOW", () => {
    // A lexical rule has no opinion about its own odds, and a card that rendered `p=0.00` for it
    // would show a very confident ALLOW that a person nonetheless had to answer.
    mount(() => {}, { ...question, decision: "review" });
    expect(screen.queryByText(/p=/)).not.toBeInTheDocument();
    expect(screen.getByText(question.reason)).toBeInTheDocument();
  });

  it("shows the band only when there is a number to read it against", () => {
    // The band without a number is a word about nothing; the model without either is a build that
    // answered a question nobody can see.
    mount(() => {}, { ...question, band: "review", decider_model: "qwen3:4b@Q4_K_M" });
    expect(screen.queryByText(/band REVIEW/)).not.toBeInTheDocument();
    expect(screen.queryByText(/qwen3:4b@Q4_K_M/)).not.toBeInTheDocument();
  });

  it("formats the decimal the way the chosen language does, not the way the machine does", () => {
    // `toFixed(2)` prints `0.80` on every machine. The sentence around this number is translated, so
    // on a pt-BR machine set to Portuguese that reads as a count of eighty, not a probability.
    localStorage.setItem("chimera.lang", "pt");
    try {
      mount(() => {}, { ...question, decision: "review", p: 0.8, band: "review" });
      expect(screen.getByText(/p=0,80/)).toBeInTheDocument();
    } finally {
      localStorage.removeItem("chimera.lang");
    }
  });

  it("an unknown band is shown as nothing, never raw and never guessed", () => {
    // Same rule the level chip follows: an unrecognised string on a risk line is worse than no line.
    mount(() => {}, { ...question, p: 0.8, band: "escalated" });
    expect(screen.getByText(/p=0\.80/)).toBeInTheDocument();
    expect(screen.queryByText(/escalated/i)).not.toBeInTheDocument();
  });

  it("rounds to the two decimals the map actually resolves", () => {
    // `0.8000000000000001` on a card reads as a precision the number does not have.
    mount(() => {}, { ...question, p: 0.8000000000000001, band: "review" });
    expect(screen.getByText(/p=0\.80/)).toBeInTheDocument();
    expect(screen.queryByText(/0\.8000000000000001/)).not.toBeInTheDocument();
  });

  it("a number with no band still renders — the band is the only thing missing", () => {
    mount(() => {}, { ...question, p: 0.8 });
    expect(screen.getByText(/p=0\.80/)).toBeInTheDocument();
    expect(screen.queryByText(/band /)).not.toBeInTheDocument();
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
