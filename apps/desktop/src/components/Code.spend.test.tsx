import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { AgentProvider } from "@/lib/agent-context";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

/**
 * The whole wire, from the box you type a number into to the number in the corner.
 *
 * `AgentConfig.max_usd` has been able to stop a loop before the call that breaks the cap since it
 * was written, and until now the only caller in the codebase was the cron dispatcher: no route
 * accepted a ceiling, so no screen could offer one. The two halves that make it a feature rather
 * than a field are the request carrying the number and the receipt carrying it back — a ceiling
 * nobody can watch being consumed is one nobody sets twice.
 */
describe("Code — a spend ceiling for the turn", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ done: { usd: 0.0123 } }));
  });

  async function screen_() {
    const user = userEvent.setup();
    renderWithProviders(
      <AgentProvider>
        <Code />
        <AgentStatusBar />
      </AgentProvider>,
    );
    return user;
  }

  const ask = (user: ReturnType<typeof userEvent.setup>, text: string) =>
    user.type(screen.getByPlaceholderText(/^Ask about this code/), `${text}{Enter}`);

  it("sends no ceiling when none was armed", async () => {
    // The inert default, asserted on the request rather than on the control: every conversation
    // that existed before this field must send exactly what it sent before, and `max_usd: null`
    // would be a new key for the server to interpret rather than the absence of one.
    const user = await screen_();

    await ask(user, "what is this?");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).not.toHaveProperty("max_usd");
  });

  it("sends the ceiling that was typed, on every turn and not only the first", async () => {
    // The agent is rebuilt per turn from this request, so a ceiling sent once is a ceiling that
    // applied once — and the turn where it bites is the late one, never the first.
    const user = await screen_();
    await user.type(screen.getByRole("spinbutton", { name: /Ceiling/ }), "0.5");

    await ask(user, "first");
    await waitFor(() => expect(screen.getByPlaceholderText(/^Ask about this code/)).toBeEnabled());
    await ask(user, "second");

    await waitFor(() => expect(vi.mocked(streamCodeTurn).mock.calls.length).toBe(2));
    expect(vi.mocked(streamCodeTurn).mock.calls.map((c) => c[0].max_usd)).toEqual([0.5, 0.5]);
  });

  it("shows what the turn spent against the ceiling it ran under", async () => {
    // The receipt half. The `done` frame reports the cost and knows nothing about the ceiling, so
    // if the conversation does not hand it over the bar shows `~ $0.0123` against nothing — which
    // is exactly the state this item started from.
    const user = await screen_();
    await user.type(screen.getByRole("spinbutton", { name: /Ceiling/ }), "0.5");

    await ask(user, "what is this?");

    expect(
      await screen.findByRole("button", { name: "~ $0.0123 of $0.5000" }),
    ).toBeInTheDocument();
  });
});
