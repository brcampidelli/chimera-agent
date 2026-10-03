/** Which projects the user has let the agent run commands in.
 *
 * Per project, and that is the whole point. Running `npm install` and the test suite is what
 * separates "wrote some files" from "built something that works" — and granting it for the folder
 * you are working in is a different decision from granting it for every folder you open next.
 *
 * **The grant lives on the server now** (study 29, P4.3), in the project registry under
 * `CHIMERA_HOME`, and every coding turn is held to it there (`assemble_registry`). It used to live
 * in this webview's `localStorage`, and reach the server only as a claim inside each request —
 * `posture.reach = workspace_shell` plus `allow_host_exec` — which the server had nothing to check
 * against. So the claim was the grant: it vanished when the webview's origin changed port, the MCP
 * bridge and the bots could not see it, and any client that sent the claim had it.
 *
 * What stays here is the OLD list, kept as the migration source. It is read once, handed to the
 * server, and never read again for any decision — the same arrangement `projects.ts` made when the
 * project list moved. The server takes the hand-over once per installation; after that a client's
 * word is not a grant, which is why a fresh install sends an empty one too: it closes the window.
 */

import { migrateShellGrants, type CodeProject } from "@/lib/api";

/** Where the grants used to live. Read for the migration; never written again. */
export const SHELL_KEY = "chimera:code:shell-projects";
/** Set once the old grants have been handed over, so the hand-over is not attempted every load. */
export const SHELL_MOVED_KEY = "chimera:code:shellGrantsMoved";

/** The folders this browser had granted before the grant moved. Unknown shapes read as none. */
export function legacyShellGrants(): string[] {
  try {
    const raw = localStorage.getItem(SHELL_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((p): p is string => typeof p === "string" && !!p)
      : [];
  } catch {
    // A corrupt or unavailable store means nobody had granted anything — the safe read, and the
    // same answer a fresh install gives.
    return [];
  }
}

/** Hand this browser's old grants to the server, once.
 *
 * Never throws: a server that is not up yet, or one that predates the route, leaves the flag unset
 * and the hand-over is tried on the next load. Until it succeeds the old grants are simply not
 * granted, which is the direction a permission may fail in — the list still loads.
 */
export async function migrateShellGrantsOnce(): Promise<void> {
  if (readFlag()) return;
  try {
    // Sent even when empty: the server takes the hand-over once, and an empty one still closes it.
    await migrateShellGrants(legacyShellGrants());
  } catch {
    return;
  }
  writeFlag();
}

/** Whether the server records a grant for this exact project. No project, no grant.
 *
 * Matched by the workspace string, the key the screens use everywhere. The server matches a turn by
 * resolved folder, so it can only be MORE generous than this reading (another spelling of the same
 * folder) — never less, which is what keeps the screen from showing a grant the run does not have.
 */
export function shellGranted(rows: readonly CodeProject[] | undefined, workspace: string): boolean {
  if (!workspace) return false;
  return (rows ?? []).some((row) => row.path === workspace && row.shell_granted === true);
}

function readFlag(): boolean {
  try {
    return localStorage.getItem(SHELL_MOVED_KEY) === "1";
  } catch {
    // Storage unavailable means the old grants are unreadable too. The server still has to hear
    // once that there is nothing coming, so this is "not done" rather than "nothing to do".
    return false;
  }
}

function writeFlag(): void {
  try {
    localStorage.setItem(SHELL_MOVED_KEY, "1");
  } catch {
    // Attempted again next load. The server answers a repeat with "already done" and changes
    // nothing, so that costs a request rather than correctness.
  }
}
