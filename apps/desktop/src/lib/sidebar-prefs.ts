/** How the Code sidebar is arranged: whether worktrees file under their project, and which project
 *  sections are folded away.
 *
 * Local to this webview, in the same place and with the same discipline as the chosen workspace and
 * each project's last conversation (`workspace.ts`): storage can be unavailable (private mode, a
 * sandboxed webview), and a preference about how a list looks is never worth throwing over. Neither
 * value says anything about the projects themselves, which is why they are not on the server with
 * the project list.
 *
 * Read at the moment of use rather than held in a module variable: Ctrl+Tab lives in `Code.tsx` and
 * the switch lives in the sidebar, and both reading the same key is what keeps the shortcut walking
 * the order the list shows after the switch is flipped.
 */

export const GROUP_WORKTREES_KEY = "chimera:code:sidebar:groupWorktrees";
export const COLLAPSED_KEY = "chimera:code:sidebar:collapsed";

/** On unless the person turned it off. Stored only when off, so "never chose" stays the default. */
export function readGroupWorktrees(): boolean {
  try {
    return localStorage.getItem(GROUP_WORKTREES_KEY) !== "0";
  } catch {
    return true;
  }
}

export function writeGroupWorktrees(on: boolean): void {
  try {
    if (on) localStorage.removeItem(GROUP_WORKTREES_KEY);
    else localStorage.setItem(GROUP_WORKTREES_KEY, "0");
  } catch {
    // The list goes back to grouping on the next launch, which is the default anyway.
  }
}

/** The group keys folded away. Unknown shapes read as nothing folded. */
export function readCollapsed(): string[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((k): k is string => typeof k === "string") : [];
  } catch {
    return [];
  }
}

export function writeCollapsed(keys: readonly string[]): void {
  try {
    if (keys.length === 0) localStorage.removeItem(COLLAPSED_KEY);
    else localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...new Set(keys)]));
  } catch {
    // The sections open again next launch: more to scroll, nothing lost.
  }
}
