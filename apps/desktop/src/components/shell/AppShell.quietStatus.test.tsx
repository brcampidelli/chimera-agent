import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/shell/AppShell";
import { listRunningTurns } from "@/lib/api";
import { AgentProvider } from "@/lib/agent-context";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api")>()),
  getApprovals: vi.fn(async () => []),
  listJobs: vi.fn(async () => []),
  listRunningTurns: vi.fn(async () => []),
  stopCodeTurn: vi.fn(),
}));
vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

/**
 * The editor has its own status bar, so the conversation's — the last turn's tokens and dollars —
 * stays off that screen while nothing is running (study 34, the Editor slice). It comes back the
 * moment something is: that bar is where a turn or a run is watched and stopped from, and hiding it
 * then would take the Stop away from the one screen the person is on.
 */
function shell(quietStatus: boolean, agent: Parameters<typeof AgentProvider>[0]["value"] = {}) {
  return renderWithProviders(
    <AgentProvider value={agent}>
      <AppShell rail={null} viewKey="edit" viewLabel="Editor" quietStatus={quietStatus}>
        <p>screen</p>
      </AppShell>
    </AgentProvider>,
  );
}

const footer = () => document.querySelector("footer");

describe("AppShell — a screen with its own status bar", () => {
  beforeEach(() => {
    vi.mocked(listRunningTurns).mockReset().mockResolvedValue([]);
  });

  it("shows the agent's bar on every other screen, idle or not", () => {
    shell(false);
    expect(footer()).not.toBeNull();
  });

  it("leaves it out while the agent is idle", async () => {
    shell(true);
    await waitFor(() => expect(listRunningTurns).toHaveBeenCalled());
    expect(footer()).toBeNull();
    expect(screen.getByText("screen")).toBeInTheDocument();
  });

  it("brings it back while a turn is running here", () => {
    shell(true, { status: "thinking", busy: true });
    expect(footer()).not.toBeNull();
  });

  it("brings it back while a turn runs in another conversation", async () => {
    vi.mocked(listRunningTurns).mockResolvedValue([
      { turn_id: "t", session_id: "s", workspace: "/w", message: "m", started_at: 0, live_since: 0, transcript_saved: false },
    ]);
    shell(true);
    await waitFor(() => expect(footer()).not.toBeNull());
  });
});
