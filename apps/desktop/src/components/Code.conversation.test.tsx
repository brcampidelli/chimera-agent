import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  deleteCodeSession,
  getFsTree,
  getGitStatus,
  getRuns,
  streamCodeTurn,
  streamRun,
} from "@/lib/api";
import { emptyTree, gitStatus, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** Type into the conversation composer and send. */
async function say(text: string) {
  const user = userEvent.setup();
  renderWithProviders(<Code />);
  await user.type(screen.getByPlaceholderText(/^Ask about this code/), text);
  await user.click(screen.getByRole("button", { name: "Send" }));
  return user;
}

describe("Code — the conversation", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
  });

  it("shows what the agent DID, not only what it said", async () => {
    // In a coding conversation the tool calls are the substance and the prose is the caption. A
    // transcript that renders only the prose is a transcript of the wrong half.
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({
        tools: [
          { name: "read_file", arguments: { path: "src/app.py" }, ok: true, observation: "x = 1" },
          { name: "run_shell", arguments: { cmd: "pytest" }, ok: false, observation: "1 failed" },
        ],
        done: { answer: "the test is broken" },
      }),
    );
    await say("why does the test fail?");

    expect(await screen.findByText("read_file")).toBeInTheDocument();
    expect(screen.getByText("src/app.py")).toBeInTheDocument();
    expect(screen.getByText("run_shell")).toBeInTheDocument();
    expect(screen.getByText("the test is broken")).toBeInTheDocument();
  });

  it("carries the session id into the next turn", async () => {
    // The whole reason the conversation exists. Drop the id and every message silently starts a new
    // conversation — the only symptom is that the agent seems to have forgotten everything.
    const user = await say("read a.py");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].session_id).toBeFalsy();

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "now rename it");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamCodeTurn).mock.calls[1][0].session_id).toBe("s1");
  });

  it("renders a real diff for each file the turn edited", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({ edits: [{ path: "src/app.py", patch: "@@ -1 +1 @@\n-old\n+new" }] }),
    );
    await say("rename the function");

    expect(await screen.findByText("src/app.py")).toBeInTheDocument();
    expect(screen.getByText("+new")).toBeInTheDocument();
  });

  it("says the price is unknown rather than showing zero", async () => {
    // The backend returns null for a model whose price it does not know, and never guesses one.
    // Rendering that as $0.0000 would turn "we don't know" into "it was free".
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ done: { usd: null } }));
    await say("hello");

    expect(await screen.findByText("price unknown")).toBeInTheDocument();
    expect(screen.queryByText("$0.0000")).not.toBeInTheDocument();
  });

  it("shows the real cost when the price IS known", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ done: { usd: 0.0123 } }));
    await say("hello");

    expect(await screen.findByText("$0.0123")).toBeInTheDocument();
  });

  it("reports a failed turn instead of leaving an empty bubble", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ error: true }));
    await say("do the thing");

    expect(await screen.findByText("That turn failed.")).toBeInTheDocument();
  });

  it("hands a failed turn to the verified run, which retries and reverts", async () => {
    // This used to be a second button in the composer, asking the user to guess in advance whether
    // the work deserved retries. It is a CONSEQUENCE now — the turn's verification failed, and the
    // multi-attempt run is one of the two things you can do about it.
    //
    // It also used to fill a form and wait for a second press, so the verify command and the attempt
    // count could be set first. Those fields no longer exist and the server infers the command from
    // the project, so waiting for a press on a form with nothing left to fill in is not consent. The
    // global status bar shows the run and can stop it from any screen.
    vi.mocked(streamCodeTurn).mockImplementation(
      scriptTurn({
        verified: { command: "make test", source: "inferred:Makefile", state: "failed" },
      }),
    );
    const user = await say("make the test pass");

    await user.click(await screen.findByRole("button", { name: /try to fix it/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    expect(vi.mocked(streamRun).mock.calls[0][0]).toMatchObject({ task: "make the test pass" });
  });

  it("forgets the conversation server-side when cleared", async () => {
    const user = await say("hello");
    await user.click(await screen.findByRole("button", { name: "Clear" }));

    await waitFor(() => expect(deleteCodeSession).toHaveBeenCalledWith("s1"));
    expect(screen.queryByText("hello")).not.toBeInTheDocument();
  });

  it("tells the user the conversation is not amnesiac before the first turn", async () => {
    renderWithProviders(<Code />);
    expect(
      await screen.findByText(/keeps its tool calls, so a follow-up doesn't start from nothing/),
    ).toBeInTheDocument();
  });
});
