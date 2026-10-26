import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserQuestionCard, isOtherOption } from "@/components/code/UserQuestionCard";
import { I18nProvider } from "@/lib/i18n";
import type { UserQuestionEvent } from "@/lib/api";

const { answerUserQuestion } = vi.hoisted(() => ({
  answerUserQuestion: vi.fn(async (_id: string, _answer: string) => ({ ok: true })),
}));
vi.mock("@/lib/api", async (original) => ({
  ...(await original<typeof import("@/lib/api")>()),
  answerUserQuestion,
}));

const base: UserQuestionEvent = {
  id: "q1",
  kind: "user_question",
  question: "Which approach?",
  options: ["Blue", "Other (describe)"],
  allow_free_text: true,
  asked_at: Date.now() / 1000,
};

function mount(question: UserQuestionEvent = base, onSendAsNext?: (text: string) => void,
  onOpenDesign?: (id: string) => void) {
  const onAnswered = vi.fn();
  render(
    <I18nProvider>
      <UserQuestionCard question={question} onAnswered={onAnswered} onSendAsNext={onSendAsNext}
        onOpenDesign={onOpenDesign} />
    </I18nProvider>,
  );
  return onAnswered;
}

beforeEach(() => {
  answerUserQuestion.mockReset();
  answerUserQuestion.mockResolvedValue({ ok: true });
});

describe("UserQuestionCard — Other (item 13)", () => {
  it("recognises the ways an agent writes 'something else'", () => {
    for (const label of ["Other (describe)", "Outra (descreva)", "Otro", "Autre", "Andere", "その他", "其他"]) {
      expect(isOtherOption(label)).toBe(true);
    }
    for (const label of ["Blue", "Otherwise run it twice", "Outrageous"]) {
      expect(isOtherOption(label)).toBe(false);
    }
  });

  it("choosing Other sends nothing, focuses the box, and sends only with text", async () => {
    const onAnswered = mount();
    await userEvent.click(screen.getByRole("button", { name: "Other (describe)" }));
    expect(answerUserQuestion).not.toHaveBeenCalled();
    const box = screen.getByRole("textbox");
    expect(box).toHaveFocus();
    const send = screen.getByRole("button", { name: /send answer/i });
    expect(send).toBeDisabled();
    await userEvent.type(box, "the green one");
    expect(send).toBeEnabled();
    await userEvent.click(send);
    await waitFor(() => expect(answerUserQuestion).toHaveBeenCalledWith("q1", "the green one"));
    expect(onAnswered).toHaveBeenCalledTimes(1);
  });

  it("shows label and description once when the legacy question duplicates the description", () => {
    mount({ ...base, label: "Choose", description: "Same prompt", question: "Same prompt" });
    expect(screen.getByRole("heading", { name: "Choose" })).toBeInTheDocument();
    expect(screen.getAllByText("Same prompt")).toHaveLength(1);
  });

  it("an ordinary option still answers at once, and a shortened one answers with its full text", async () => {
    mount({ ...base, options: ["Blue…"], option_details: { "Blue…": "Blue, all of it" } });
    await userEvent.click(screen.getByRole("button", { name: "Blue…" }));
    await waitFor(() => expect(answerUserQuestion).toHaveBeenCalledWith("q1", "Blue, all of it"));
  });

  it("design approval shows its document and revision; the buttons submit and the panel opens", async () => {
    const onOpenDesign = vi.fn();
    mount({ ...base, kind: "design_approval", title: "Launch deck", revision: 4,
      document_id: "design-1", description: "A bold launch screen", options: ["approved", "adjustments"] },
      undefined, onOpenDesign);
    expect(screen.getByText("Launch deck — Revision 4")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open design" }));
    expect(onOpenDesign).toHaveBeenCalledWith("design-1");
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(answerUserQuestion).toHaveBeenCalledWith("q1", "approved"));
  });

  it("a design adjustment sends the owner's note back to the paused turn", async () => {
    mount({ ...base, kind: "design_approval", title: "Launch deck", revision: 4,
      document_id: "design-1", options: ["approved", "adjustments"] });
    await userEvent.type(screen.getByRole("textbox"), "Increase contrast");
    await userEvent.click(screen.getByRole("button", { name: "Request changes" }));
    await waitFor(() => expect(answerUserQuestion).toHaveBeenCalledWith("q1", "adjustments: Increase contrast"));
  });

  it("without free text, an 'Other' label is just an option", async () => {
    mount({ ...base, allow_free_text: false });
    await userEvent.click(screen.getByRole("button", { name: "Other (describe)" }));
    await waitFor(() => expect(answerUserQuestion).toHaveBeenCalledWith("q1", "Other (describe)"));
  });
});

describe("UserQuestionCard — an expired question (item 14)", () => {
  it("an answer the server no longer takes turns the card into 'expired', keeping the text", async () => {
    answerUserQuestion.mockResolvedValue({ ok: false });
    const onSendAsNext = vi.fn();
    mount(base, onSendAsNext);
    await userEvent.type(screen.getByRole("textbox"), "my long answer");
    await userEvent.click(screen.getByRole("button", { name: /send answer/i }));
    expect(await screen.findByText(/expired before your answer arrived/i)).toBeInTheDocument();
    // Not the generic failure, and no second chance at a question nobody is waiting on.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /send answer/i })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue("my long answer");
    await userEvent.click(screen.getByRole("button", { name: /send as the next message/i }));
    expect(onSendAsNext).toHaveBeenCalledWith("my long answer");
  });

  it("choosing Other without typing never submits an empty answer", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Other (describe)" }));
    expect(answerUserQuestion).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /send answer/i })).toBeDisabled();
  });

  it("a question past its wait is expired on arrival", () => {
    mount({ ...base, asked_at: Date.now() / 1000 - 400, wait_seconds: 300 });
    expect(screen.getByText(/expired before your answer arrived/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send as the next message/i })).toBeDisabled();
  });

  it("a card that was open expires on its own when the wait runs out", async () => {
    vi.useFakeTimers();
    try {
      mount({ ...base, asked_at: Date.now() / 1000, wait_seconds: 30 });
      expect(screen.queryByText(/expired before your answer arrived/i)).not.toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(31_000);
      });
      expect(screen.getByText(/expired before your answer arrived/i)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a network failure is still the generic one, with the form kept", async () => {
    answerUserQuestion.mockRejectedValue(new Error("offline"));
    mount();
    await userEvent.type(screen.getByRole("textbox"), "hello");
    await userEvent.click(screen.getByRole("button", { name: /send answer/i }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send answer/i })).toBeInTheDocument();
  });
});
