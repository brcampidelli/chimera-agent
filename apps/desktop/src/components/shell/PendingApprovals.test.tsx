import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Governance } from "@/components/Governance";
import { PendingApprovals } from "@/components/shell/PendingApprovals";
import { answerApproval, getApprovals } from "@/lib/api";
import { APPROVALS_POLL_MS } from "@/lib/usePendingApprovals";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getApprovals: vi.fn(async () => []),
  answerApproval: vi.fn(async () => ({ ok: true })),
  // Governance is mounted in the last test, to measure that it no longer owns a timer of its own.
  // These four are the rest of what that screen asks for; an undefined answer leaves each panel in
  // its loading state, which is fine for a test that only counts calls to `getApprovals`.
  getConfig: vi.fn(async () => ({ autonomy: { governance: "off" } })),
  getGovernanceAudit: vi.fn(),
  getGovernanceInjection: vi.fn(),
  getSandboxState: vi.fn(),
}));

/** What `GET /api/approvals` returns per parked question. `ApprovalOut` carries `asked_at` and
 *  `age_seconds` and NO `wait_seconds` — the list does not say how long the turn will wait. */
function question(id = "q1") {
  return {
    id,
    action: "write_file(path='report.md')",
    reason: "write_file is restricted after this run consumed untrusted content",
    asked_at: Math.floor(Date.now() / 1000) - 12,
    age_seconds: 12,
    decision: "review",
  };
}

/**
 * A parked question, seen from a screen that is not Governance.
 *
 * The thing under test is not a badge. Governance can stop a tool call and ask a person; silence
 * refuses after fifteen minutes (`chimera/governance/pending.py`), so a question that is visible
 * from one screen out of seven is a refusal that gets made by nobody. The chip lives in the status
 * bar, which is mounted under every view.
 */
describe("PendingApprovals — the question follows you", () => {
  beforeEach(() => {
    vi.mocked(getApprovals).mockReset();
    vi.mocked(getApprovals).mockResolvedValue([]);
    vi.mocked(answerApproval).mockReset();
    vi.mocked(answerApproval).mockResolvedValue({ ok: true });
  });

  it("renders nothing at all while no question is parked", async () => {
    const { container } = renderWithProviders(<PendingApprovals />);

    // Not an empty chip and not a `0`. An indicator that is on screen every day of the year is one
    // people stop seeing, and this one has to be worth a glance the day it matters.
    await waitFor(() => expect(getApprovals).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows how many are waiting, and opens the question itself", async () => {
    vi.mocked(getApprovals).mockResolvedValue([question()]);
    const user = userEvent.setup();
    renderWithProviders(<PendingApprovals />);

    const chip = await screen.findByRole("button", { name: /waiting on you: 1/i });
    // The digits are visible; the sentence is the accessible name. A voice user says what they see.
    expect(chip).toHaveTextContent("1");

    await user.click(chip);

    // The ledger's own sentence, verbatim — this is `ApprovalCard`, not a second rendering of it.
    expect(await screen.findByText(question().reason)).toBeInTheDocument();
  });

  it("approving answers that question by id, through the one answering path there is", async () => {
    vi.mocked(getApprovals).mockResolvedValue([question("q-abc")]);
    const user = userEvent.setup();
    renderWithProviders(<PendingApprovals />);

    await user.click(await screen.findByRole("button", { name: /waiting on you/i }));
    await user.click(await screen.findByRole("button", { name: /allow this once/i }));

    await waitFor(() => expect(answerApproval).toHaveBeenCalledWith("q-abc", true));
  });

  it("shows no deadline for a listed question, because the list does not carry one", async () => {
    // `ApprovalCard` counts a deadline down and, at zero, drops the buttons and says silence
    // answered (#416). It can only do that from `wait_seconds`, and `ApprovalOut` has no such
    // field: the list endpoint says when a question was ASKED, never how long its turn will wait.
    //
    // So the card's own fallback applies here — no line rather than a number it does not have —
    // and this test exists to keep it that way. The 900-second default lives in
    // `chimera/governance/pending.py` as a module constant that `ask_durably` accepts an override
    // for, so a countdown rendered from it would be arithmetic on an assumption, presented as a
    // fact, on the one surface whose entire job is to not do that. Fixing this belongs in
    // `ApprovalOut`, not here.
    vi.mocked(getApprovals).mockResolvedValue([question()]);
    const user = userEvent.setup();
    renderWithProviders(<PendingApprovals />);

    await user.click(await screen.findByRole("button", { name: /waiting on you/i }));

    expect(await screen.findByText(question().reason)).toBeInTheDocument();
    expect(screen.queryByText(/silence refuses/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/silence refused/i)).not.toBeInTheDocument();
    // And the two answers are still offered, which is the correct reading of "no deadline known".
    expect(screen.getByRole("button", { name: /allow this once/i })).toBeInTheDocument();
  });

  it("refusing answers with false — a refusal is a decision, not the absence of one", async () => {
    vi.mocked(getApprovals).mockResolvedValue([question("q-abc")]);
    const user = userEvent.setup();
    renderWithProviders(<PendingApprovals />);

    await user.click(await screen.findByRole("button", { name: /waiting on you/i }));
    await user.click(await screen.findByRole("button", { name: /^refuse$/i }));

    await waitFor(() => expect(answerApproval).toHaveBeenCalledWith("q-abc", false));
  });
});

describe("PendingApprovals — staleness", () => {
  beforeEach(() => {
    vi.mocked(getApprovals).mockReset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stops offering a question that expired or was answered elsewhere", async () => {
    // Nothing here computes an expiry, and that is the point: the app is told, it does not guess.
    // `ApprovalOut` carries no deadline — only `age_seconds` — so a countdown drawn here would be
    // arithmetic on a constant the API never sends. What actually happens is that the waiting
    // thread deletes the request file when it is answered or when silence refuses it, and the very
    // next poll returns a shorter list.
    vi.mocked(getApprovals)
      .mockResolvedValueOnce([question("q-gone")])
      .mockResolvedValue([]);
    renderWithProviders(<PendingApprovals />);

    const chip = await vi.waitFor(() =>
      screen.getByRole("button", { name: /waiting on you: 1/i }),
    );
    // `fireEvent`, not `userEvent`: userEvent's own timers and vitest's fake ones deadlock on the
    // dialog's mount, and the click is incidental here — the subject is what the next poll does.
    fireEvent.click(chip);
    expect(await vi.waitFor(() => screen.getByText(question().reason))).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(APPROVALS_POLL_MS + 50);
    });

    // The chip goes, and so does the open dialog: leaving it up would offer a decision on something
    // that has already been decided, and the button under the cursor would be a lie.
    await vi.waitFor(() =>
      expect(screen.queryByRole("button", { name: /waiting on you/i })).not.toBeInTheDocument(),
    );
    expect(screen.queryByText(question().reason)).not.toBeInTheDocument();
  });

  it("polls the endpoint itself, so a question asked in another window still arrives", async () => {
    vi.mocked(getApprovals).mockResolvedValue([]);
    renderWithProviders(<PendingApprovals />);

    await vi.waitFor(() => expect(getApprovals).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(APPROVALS_POLL_MS * 3 + 50);
    });

    expect(vi.mocked(getApprovals).mock.calls.length).toBeGreaterThan(1);
  });

  it("is the only timer on the endpoint — Governance reads the same query, it does not poll", async () => {
    // Governance polled `/api/approvals` every two seconds from its own screen. Now that the same
    // list is in the status bar, which is mounted under Governance as well as everywhere else, a
    // second `refetchInterval` on the same key would be a second timer on one endpoint — React
    // Query only coalesces the fetches that happen to overlap in flight.
    vi.mocked(getApprovals).mockResolvedValue([]);
    renderWithProviders(<Governance />);

    await vi.waitFor(() => expect(getApprovals).toHaveBeenCalledTimes(1));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(APPROVALS_POLL_MS * 5 + 50);
    });

    expect(getApprovals).toHaveBeenCalledTimes(1);
  });
});
