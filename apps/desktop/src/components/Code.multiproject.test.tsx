import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  streamCodeTurn,
} from "@/lib/api";
import { useRunSession } from "@/lib/run-session";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
vi.mock("@/lib/run-session", async () => {
  const actual = await vi.importActual<typeof import("@/lib/run-session")>("@/lib/run-session");
  return { ...actual, useRunSession: vi.fn(actual.useRunSession) };
});

const IDLE = {
  running: false,
  task: "",
  runId: null,
  events: [],
  done: null,
  stopping: false,
  broken: false,
  workspace: null,
  paused: null,
  verify: null,
  start: () => {},
  stop: () => {},
  clearPaused: () => {},
};

function runningIn(workspace: string | null) {
  vi.mocked(useRunSession).mockReturnValue({ ...IDLE, running: true, workspace });
}

/** Render, and put something in the composer.
 *
 * The typing is not decoration: Send is also disabled on an empty draft, so asserting on a blank
 * composer would have passed for the wrong reason in the two cases where blocking is correct — and
 * a test that cannot fail is worse than no test, because it reads like coverage.
 */
async function withDraft(user: ReturnType<typeof userEvent.setup>) {
  renderWithProviders(<Code />);
  await user.type(await screen.findByPlaceholderText(/^Ask about this code/), "hello");
  return screen.getByRole("button", { name: "Send" });
}

/**
 * Two defects that are invisible with one project and routine with several — which is why neither
 * was found by a suite that only ever had one.
 */
// `delay: null` on every `setup` here. This file types three sentences and a path through
// `user.type`, which simulates a keystroke at a time with a delay between them; isolated that is
// ~1.5s, and under a full parallel suite it crossed the 5s per-test budget and started failing
// consistently. The delay models human typing speed, which none of these tests assert anything
// about — what they assert is which `session_id` each turn carries.
describe("Code — with more than one project", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ session: "s1" }));
  });

  async function send(user: ReturnType<typeof userEvent.setup>, message: string) {
    await user.type(screen.getByPlaceholderText(/^Ask about this code/), message);
    await user.click(screen.getByRole("button", { name: "Send" }));
  }

  it("does not carry the conversation into the project you switched to", async () => {
    // The server fixes a conversation's project when the conversation is created and never moves
    // it — deliberately, so a conversation does not migrate between groups in the sidebar. So
    // keeping the id across a project change left the next turn writing into a conversation filed
    // under the OLD project: the screen said one thing, the disk said another, and the disk was
    // right.
    const user = userEvent.setup({ delay: null });
    renderWithProviders(<Code />);

    await send(user, "what is here?");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledOnce());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].session_id).toBeNull();

    // A second turn continues the same conversation — this is the control.
    await send(user, "and now?");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamCodeTurn).mock.calls[1][0].session_id).toBe("s1");

    // Switching project must start over.
    const path = screen.getByPlaceholderText(/folder path/i);
    await user.clear(path);
    await user.type(path, "/other-project");
    await user.click(screen.getByRole("button", { name: "Open" }));

    await send(user, "what about here?");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(3));
    expect(vi.mocked(streamCodeTurn).mock.calls[2][0].session_id).toBeNull();
    expect(vi.mocked(streamCodeTurn).mock.calls[2][0].workspace).toBe("/other-project");
  });

  it("a run in another project does not stop you typing in this one", async () => {
    // The reason this screen blocks while a run is live is that a turn and a run editing the same
    // directory would race. That stops being true the moment the run is somewhere else, and
    // blocking anyway is a lie about why.
    runningIn("/some-other-project");
    expect(await withDraft(userEvent.setup({ delay: null }))).not.toBeDisabled();
  });

  it("a run in THIS project still does", async () => {
    // "" is the screen's project here: a fresh app has chosen none and the server falls back to its
    // own workspace. Same project, empty or not, is the case the block exists for.
    runningIn("");
    expect(await withDraft(userEvent.setup({ delay: null }))).toBeDisabled();
  });

  it("a run whose project is unknown still blocks", async () => {
    // "We cannot tell which directory it is editing" is a reason to be careful, not a reason to
    // allow — the unknown case has to fail towards the safe answer.
    runningIn(null);
    expect(await withDraft(userEvent.setup({ delay: null }))).toBeDisabled();
  });
});
