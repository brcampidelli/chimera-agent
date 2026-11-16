import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { streamRun, type RunStreamHandlers } from "@/lib/api";
import { AgentProvider } from "@/lib/agent-context";
import { useRunSession } from "@/lib/run-session";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  streamRun: vi.fn(),
  cancelRun: vi.fn(),
  getApprovals: vi.fn(async () => []),
  listRunningTurns: vi.fn(async () => []),
  stopCodeTurn: vi.fn(),
}));
vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

function Launcher() {
  const run = useRunSession();
  return <button onClick={() => run.start({ task: "fix it", max_attempts: 1 })}>go</button>;
}

const live = () => screen.getByRole("status").getAttribute("aria-live");

/**
 * One announcer per transition, and never none. On the chat screen the header chip in
 * `Conversation` says the turn started, waited, failed or finished, and this bar stays quiet so it is
 * not said twice, but ONLY while that chip is on screen and no run is involved.
 */
describe("AgentStatusBar — live region", () => {
  it("is silent on the chat screen while the conversation's own chip is on screen", () => {
    renderWithProviders(
      <AgentProvider value={{ chatAnnounces: true }}>
        <AgentStatusBar viewKey="code" />
      </AgentProvider>,
    );
    expect(live()).toBe("off");
  });

  it("is the announcer on every other screen", () => {
    renderWithProviders(
      <AgentProvider value={{ chatAnnounces: true }}>
        <AgentStatusBar viewKey="work" />
      </AgentProvider>,
    );
    expect(live()).toBe("polite");
  });

  it("speaks when the conversation is hidden (the viewer is maximised), chip or no chip", () => {
    renderWithProviders(
      <AgentProvider value={{ chatAnnounces: false }}>
        <AgentStatusBar viewKey="code" />
      </AgentProvider>,
    );
    expect(live()).toBe("polite");
  });

  it("stays live through the END of a run, so its completion is announced", async () => {
    const user = userEvent.setup();
    let captured!: RunStreamHandlers;
    vi.mocked(streamRun).mockImplementation((_req, handlers: RunStreamHandlers) => {
      captured = handlers;
      return new Promise<void>(() => {});
    });
    renderWithProviders(
      <AgentProvider value={{ chatAnnounces: true }}>
        <Launcher />
        <AgentStatusBar viewKey="code" />
      </AgentProvider>,
    );
    expect(live()).toBe("off");

    await user.click(screen.getByText("go"));
    expect(live()).toBe("polite");

    await act(async () => {
      captured.onDone?.({ success: true, answer: "", attempts: 1, stopped_reason: "" });
    });
    // The region must not flip to "off" in the render that says the run is over.
    expect(live()).toBe("polite");
  });
});
