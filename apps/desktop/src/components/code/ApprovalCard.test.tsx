import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
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
