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

function mount(question: UserQuestionEvent = base, onSendAsNext?: (text: string) => void) {
  const onAnswered = vi.fn();
  render(
    <I18nProvider>
      <UserQuestionCard question={question} onAnswered={onAnswered} onSendAsNext={onSendAsNext} />
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

  it("an ordinary option still answers at once, and a shortened one answers with its full text", async () => {
    mount({ ...base, options: ["Blue…"], option_details: { "Blue…": "Blue, all of it" } });
    await userEvent.click(screen.getByRole("button", { name: "Blue…" }));
    await waitFor(() => expect(answerUserQuestion).toHaveBeenCalledWith("q1", "Blue, all of it"));
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
