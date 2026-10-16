import type { BackgroundJob, CodeTurnQueueItem, McpServer } from "@/lib/types";

/**
 * The decisions the side cards (C5) make, as functions with no screen, so each is tested as a rule.
 */

/** How many changed files the Changes card names before it says "and N more". */
export const CHANGES_SHOWN = 5;

/** The last segment of a folder path, either separator; the path itself when it has none. */
export function folderName(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const parts = trimmed.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/** A path for comparing: forward slashes, no trailing one, and lower case for a Windows drive path,
 *  whose case the file system ignores. */
function comparable(path: string): string {
  const forward = path.replace(/\\/g, "/").replace(/\/+$/, "");
  return /^[a-zA-Z]:/.test(forward) ? forward.toLowerCase() : forward;
}

/** Whether `path` is `root` or inside it. Never true for an empty root: a conversation with no folder
 *  has no jobs of its own, and "" would otherwise contain every path. */
export function isInsideFolder(path: string, root: string): boolean {
  if (!root) return false;
  const p = comparable(path);
  const r = comparable(root);
  return p === r || p.startsWith(`${r}/`);
}

export type ConnectorState = "paused" | "held" | "passed" | "failed" | "untested";

/** One server's state from what the list says. The last test is history (`mcp_api.list_servers`):
 *  "passed" means its last test passed, never that it is connected now. */
export function connectorState(server: McpServer): ConnectorState {
  if (server.disabled) return "paused";
  if (server.manifest_held) return "held";
  if (!server.last_test) return "untested";
  return server.last_test.ok ? "passed" : "failed";
}

/** The servers worth testing again: switched on, not held, and failed their last test. A paused
 *  server is the owner's choice and a held one waits for an approval; neither is a failure. */
export function toRetest(servers: readonly McpServer[]): McpServer[] {
  return servers.filter((s) => connectorState(s) === "failed");
}

/** How often the durable queue is read, in ms, or false. Never with the window hidden; often while it
 *  has something waiting or running, since that is when it changes; rarely otherwise, because an item
 *  can arrive from another client (the terminal, the bridge, a guest link) without this screen doing
 *  anything. */
export function queuePollInterval(items: readonly CodeTurnQueueItem[] | undefined, hidden: boolean): number | false {
  if (hidden) return false;
  return items?.some((i) => i.state === "pending" || i.state === "running") ? 10_000 : 60_000;
}

export interface TaskCounts {
  jobs: BackgroundJob[];
  queueRunning: number;
  queueWaiting: number;
  works: number;
  /** Everything running now: jobs, the queued turn being worked, and works. */
  running: number;
}

/** What the Tasks card counts for one folder and one conversation. */
export function taskCounts(
  jobs: readonly BackgroundJob[],
  queue: readonly CodeTurnQueueItem[],
  worksActive: number,
  workspace: string,
): TaskCounts {
  const mine = jobs.filter((j) => j.state === "running" && isInsideFolder(j.cwd, workspace));
  const queueRunning = queue.filter((i) => i.state === "running").length;
  const queueWaiting = queue.filter((i) => i.state === "pending").length;
  return {
    jobs: mine,
    queueRunning,
    queueWaiting,
    works: worksActive,
    running: mine.length + queueRunning + worksActive,
  };
}
