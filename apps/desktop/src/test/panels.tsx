import type { ReactNode } from "react";
import { act } from "@testing-library/react";

import { ConversationSideProvider, type ConversationSide } from "@/lib/conversation-side";
import type { BackgroundJob } from "@/lib/types";
import { gitStatus } from "@/test/code-api-mock";

/**
 * Every dock panel with something to show.
 *
 * A panel with nothing to show is not drawn at all, frame included, whether it is open, minimised or a
 * tab in a group (`useSilentPanels`). A test about what a panel does when moved, grouped, split or
 * closed needs the panel on screen, so it mounts the shell with these: the agent after a turn
 * (`AGENT_AFTER_A_TURN`), a conversation on screen (`WithConversation`), and the server reads below.
 * What each panel does with nothing to show is asserted in `Dock.test.tsx`.
 *
 * Before 0.69.1 these tests passed with most panels empty, because jsdom applies no CSS: the frame of
 * an empty panel was in the DOM, hidden only by a `:has()` rule a test never evaluated. That is the
 * same blind spot that let the owner see empty "Tools" and "Background" cards.
 */
export const SIDE_WITH_CONTENT: Partial<ConversationSide> = {
  workspace: "C:\\work\\shop",
  sessionId: null,
  posture: { reach: "workspace", approval: "suspicious" },
  peak: { used: 1_000, window: 10_000 },
};

export function WithConversation({ children, value = SIDE_WITH_CONTENT }: { children: ReactNode; value?: Partial<ConversationSide> }) {
  return <ConversationSideProvider value={value}>{children}</ConversationSideProvider>;
}

export const A_RUNNING_JOB: BackgroundJob = {
  id: "job1",
  command: "npm run bench",
  cwd: "C:\\work\\shop",
  exit_code: null,
  extra: {},
  finished_at: null,
  log: "",
  max_runtime: 0,
  owner: "",
  pid: 1,
  reported: false,
  started_at: 1,
  state: "running",
  tail: "",
};

/** The API reads that give every server-backed panel something to show. Spread into a `vi.mock` of
 *  `@/lib/api`, after the module's own exports. */
export const PANEL_API = {
  getApprovals: async () => [],
  listJobs: async () => ({ jobs: [A_RUNNING_JOB] }),
  getResources: async () => ({
    cpu_percent: 12.5,
    cpu_count: 8,
    memory: { total_mb: 16_000, used_mb: 8_000, percent: 50 },
    process_mb: 38,
    gpus: [],
    notes: [],
  }),
  getMcpServers: async () => ({
    count: 1,
    servers: [{ name: "github", command: "npx", args: [], env_keys: [], disabled: false, last_test: null, manifest_held: null }],
  }),
  getGitStatus: async () => gitStatus({ files: [{ path: "src/a.ts", x: " ", y: "M", staged: false, untracked: false }] }),
};

/** Lets the server reads above land: a few turns of the event loop, inside `act`. */
export async function settled(): Promise<void> {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}
