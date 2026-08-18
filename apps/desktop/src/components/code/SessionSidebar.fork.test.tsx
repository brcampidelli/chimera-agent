import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SessionSidebar } from "@/components/code/SessionSidebar";
import { forkCodeSession, getCodeSessionRaw, listCodeSessions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  listCodeSessions: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
}));

function session(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "fix the login redirect",
    workspace: "/home/me/chimera-agent",
    turns: 2,
    updated_at: 0,
    ...over,
  };
}

function render(onResume = vi.fn()) {
  renderWithProviders(
    <SessionSidebar
      workspace=""
      activeSession={null}
      onResume={onResume}
      onNew={vi.fn()}
      onProject={vi.fn()}
    />,
  );
  return onResume;
}

/**
 * A conversation is a linear message list that each turn replaces, so trying a different approach
 * costs the thread you were on. Duplicating is the way out of that, and looking at the stored file
 * is the way to answer "why is this conversation not what I think it is" — which every other view
 * of a session hides, because they all show it after a parse.
 */
describe("SessionSidebar — duplicating and inspecting", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([session()] as never);
  });

  it("opens the duplicate, not the conversation it came from", async () => {
    // The whole point. Landing back in the parent means the next thing typed goes into the
    // conversation the user was trying to leave untouched.
    const user = userEvent.setup();
    vi.mocked(forkCodeSession).mockResolvedValue(session({ id: "s2" }) as never);
    const onResume = render();

    await user.click(await screen.findByRole("button", { name: /Duplicate fix the login redirect/ }));

    await waitFor(() => expect(onResume).toHaveBeenCalledTimes(1));
    // The first argument only: react-query passes a mutationFn a second context argument, so
    // asserting the whole call fails on a mutation that fired exactly right.
    expect(vi.mocked(forkCodeSession).mock.calls[0][0]).toBe("s1");
    expect(onResume.mock.calls[0][0].id).toBe("s2");
  });

  it("shows the stored file, indented for reading", async () => {
    const user = userEvent.setup();
    vi.mocked(getCodeSessionRaw).mockResolvedValue({
      id: "s1",
      // One line, exactly as `save()` writes it — indentation is the screen's job.
      text: '{"session_id":"s1","messages":[{"role":"user","content":"hi"}]}',
      bytes: 62,
    } as never);
    render();

    await user.click(await screen.findByRole("button", { name: /Show the JSON for fix the login/ }));

    const shown = await screen.findByText(/"session_id": "s1"/);
    expect(shown.textContent).toContain('\n  "messages"');
    expect(screen.getByText("62 bytes on disk")).toBeInTheDocument();
  });

  it("shows a file that does not parse instead of swallowing it", async () => {
    // The reason someone opens this at all is usually that something is wrong. A pretty-printer
    // that renders nothing for a malformed file hides exactly what was being looked for.
    const user = userEvent.setup();
    vi.mocked(getCodeSessionRaw).mockResolvedValue({
      id: "s1",
      text: '{"messages": [tru',
      bytes: 17,
    } as never);
    render();

    await user.click(await screen.findByRole("button", { name: /Show the JSON for/ }));

    expect(await screen.findByText('{"messages": [tru')).toBeInTheDocument();
  });

  it("does not read a session's file until one is asked for", async () => {
    // Every Code screen mounts this sidebar. A query that ran on mount would fetch the full stored
    // transcript of whichever conversation was listed first, on every visit to the screen.
    render();

    await screen.findByText("fix the login redirect");
    expect(getCodeSessionRaw).not.toHaveBeenCalled();
  });
});
