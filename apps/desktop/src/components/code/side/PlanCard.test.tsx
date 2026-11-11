import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { PlanCard } from "@/components/code/side/PlanCard";
import { answerApproval } from "@/lib/api";
import { ConversationSideProvider, type ConversationSide, type PlanFacts } from "@/lib/conversation-side";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  answerApproval: vi.fn(),
}));

/**
 * The Plan card (C5). It reads the plan the conversation publishes and answers the question it is
 * parked on through `answerApproval`, the same call, with the same question id, as the approval card.
 */
const STEPS = ["Ler src/a.ts", "Editar src/a.ts"];

function plan(overrides: Partial<PlanFacts> = {}): PlanFacts {
  return {
    steps: STEPS,
    todos: [],
    awaitingId: null,
    status: "approved",
    clearAwaiting: vi.fn(),
    ...overrides,
  };
}

function mount(side: Partial<ConversationSide>) {
  return renderWithProviders(
    <ConversationSideProvider value={{ workspace: "C:\\work\\shop", sessionId: "s1", ...side }}>
      <PlanCard />
    </ConversationSideProvider>,
  );
}

beforeEach(() => {
  vi.mocked(answerApproval).mockReset();
});

describe("PlanCard", () => {
  it("draws nothing with no plan and no approval waiting", () => {
    const { container } = mount({ plan: null, approvalWaiting: false });
    expect(container).toBeEmptyDOMElement();
  });

  it("points at the approval card when a turn waits on one with no plan to show", () => {
    mount({ plan: null, approvalWaiting: true });
    expect(screen.getByText("The turn is waiting for your approval; see the card.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve plan" })).toBeNull();
  });

  it("lists the steps numbered and, while awaiting, offers the two answers", () => {
    mount({ plan: plan({ awaitingId: "q-1", status: "awaiting" }), approvalWaiting: true });

    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(STEPS);
    expect(screen.getByRole("button", { name: "Approve plan" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask for changes" })).toBeInTheDocument();
  });

  it("ties both buttons to the hint text with aria-describedby", () => {
    mount({ plan: plan({ awaitingId: "q-1", status: "awaiting" }), approvalWaiting: true });
    const hint = screen.getByText(/Nothing has run yet/);
    expect(hint.id).not.toBe("");
    expect(screen.getByRole("button", { name: "Approve plan" })).toHaveAttribute("aria-describedby", hint.id);
    expect(screen.getByRole("button", { name: "Ask for changes" })).toHaveAttribute("aria-describedby", hint.id);
  });

  it("approves through the same question id, records the decision, and moves focus to the title", async () => {
    vi.mocked(answerApproval).mockResolvedValue({ ok: true, outcome: "approved" } as never);
    const facts = plan({ awaitingId: "q-1", status: "awaiting" });
    mount({ plan: facts, approvalWaiting: true });

    await userEvent.click(screen.getByRole("button", { name: "Approve plan" }));

    await waitFor(() => expect(facts.clearAwaiting).toHaveBeenCalledWith("q-1", "approved"));
    expect(answerApproval).toHaveBeenCalledWith("q-1", true);
    expect(screen.getByRole("heading", { name: "Plan" })).toHaveFocus();
  });

  it("asks for changes as a refusal of the same question", async () => {
    vi.mocked(answerApproval).mockResolvedValue({ ok: true, outcome: "refused" } as never);
    const facts = plan({ awaitingId: "q-1", status: "awaiting" });
    mount({ plan: facts, approvalWaiting: true });

    await userEvent.click(screen.getByRole("button", { name: "Ask for changes" }));

    await waitFor(() => expect(facts.clearAwaiting).toHaveBeenCalledWith("q-1", "refused"));
    expect(answerApproval).toHaveBeenCalledWith("q-1", false);
  });

  it("says an already-answered question was not sent, and clears nothing", async () => {
    vi.mocked(answerApproval).mockResolvedValue({ ok: false, outcome: "no_such_request" } as never);
    const facts = plan({ awaitingId: "q-1", status: "awaiting" });
    mount({ plan: facts, approvalWaiting: true });

    await userEvent.click(screen.getByRole("button", { name: "Approve plan" }));

    expect(await screen.findByText("This question was already answered, so nothing was sent.")).toBeInTheDocument();
    expect(facts.clearAwaiting).not.toHaveBeenCalled();
  });

  it("keeps the question on screen when it was asked from another process", async () => {
    vi.mocked(answerApproval).mockResolvedValue({ ok: true, outcome: "needs_code" } as never);
    const facts = plan({ awaitingId: "q-1", status: "awaiting" });
    mount({ plan: facts, approvalWaiting: true });

    await userEvent.click(screen.getByRole("button", { name: "Approve plan" }));

    await waitFor(() => expect(answerApproval).toHaveBeenCalled());
    expect(facts.clearAwaiting).not.toHaveBeenCalled();
  });

  it("shows an approved plan with its status, its progress, and no answers", () => {
    mount({ plan: plan({ status: "approved", todos: [{ task: "Ler o arquivo", status: "done" }] }) });

    expect(screen.getByRole("status")).toHaveTextContent("Approved");
    expect(screen.queryByRole("button", { name: "Approve plan" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask for changes" })).toBeNull();
    expect(screen.getByText("Ler o arquivo")).toBeInTheDocument();
  });

  it("says a refused plan ran nothing", () => {
    mount({ plan: plan({ status: "refused" }) });
    expect(screen.getByRole("status")).toHaveTextContent("Refused — nothing ran");
  });
});
