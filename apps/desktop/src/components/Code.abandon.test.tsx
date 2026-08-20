import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * What a stopped turn leaves behind.
 *
 * Found by using the app: pressing Stop returned the composer and nothing else. Aborting the fetch
 * means no `done` frame ever arrives, so the receipt — which already knows how to say "cancelled" —
 * was never drawn, and the question sat there with no answer and no explanation, indistinguishable
 * from a turn that had silently failed.
 */
async function startAndStop() {
  const user = userEvent.setup();
  // A turn that never lands: exactly what Stop is for.
  vi.mocked(streamCodeTurn).mockImplementation(() => new Promise<void>(() => {}));
  renderWithProviders(<Code />);
  await user.type(screen.getByPlaceholderText(/^Ask about this code/), "reescreva o site");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(screen.getByRole("button", { name: /stop/i })).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: /stop/i }));
  return user;
}

describe("Code — stopping a turn", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
  });

  it("says the turn was stopped instead of leaving the question unanswered", async () => {
    await startAndStop();

    await waitFor(() =>
      expect(screen.getByText(/You stopped this turn/i)).toBeInTheDocument(),
    );
  });

  it("does not dress a decision up as a failure", async () => {
    await startAndStop();

    await waitFor(() => expect(screen.getByText(/You stopped this turn/i)).toBeInTheDocument());
    // Nothing went wrong. The error line belongs to turns that broke, and showing it here would
    // send someone looking for a cause that does not exist.
    expect(screen.queryByText(/the turn failed/i)).toBeNull();
  });

  it("gives the composer back", async () => {
    await startAndStop();

    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /stop/i })).toBeNull();
  });

  it("leaves a turn that finished alone", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ done: { answer: "pronto" } }));
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "oi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByText("pronto")).toBeInTheDocument());
    // A turn that landed has its answer. Marking it stopped would be a lie about work already
    // done and already paid for.
    expect(screen.queryByText(/You stopped this turn/i)).toBeNull();
  });
});

describe("Code — a turn with nothing to show yet", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
  });

  it("says it is working, instead of showing an empty space", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(() => new Promise<void>(() => {}));
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "pergunta");
    await user.click(screen.getByRole("button", { name: "Send" }));

    // Under fusion no token frames arrive at all, so without this a fifteen-minute turn and a dead
    // one look identical: the question, and below it nothing.
    await waitFor(() => expect(screen.getByText(/Working/i)).toBeInTheDocument());
  });

  it("stops saying it once the answer arrives", async () => {
    const user = userEvent.setup();
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ done: { answer: "pronto" } }));
    renderWithProviders(<Code />);
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "oi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(screen.getByText("pronto")).toBeInTheDocument());
    expect(screen.queryByText(/^Working…$/i)).toBeNull();
  });
});
