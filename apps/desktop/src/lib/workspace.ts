/** The chosen workspace root, remembered across launches.
 *
 * The Code screen used to start every session with an empty workspace, which makes the backend fall
 * back to its own launch directory. For `chimera app` in a terminal that is the directory you were
 * standing in, so it is right by accident. For a packaged desktop build it is wherever the shortcut
 * points — `C:\Program Files\Chimera` for an installed app. That is not the user's project, and on
 * Windows it is not writable without elevation, so the agent's edits fail there in a way that reads
 * like a bug in the agent rather than a wrong root.
 *
 * Picking the folder once and having it forgotten on the next launch is the actual defect: the
 * screen is usable, it just does not remember. So this stores the choice and nothing else — it does
 * not guess a default, because a wrong guess would silently widen where the agent may write, and
 * "somewhere the user did not choose" is exactly the property that must not be inferred.
 *
 * Follows the same discipline as `theme.ts`: storage can be unavailable (private mode, a sandboxed
 * webview) and a preference is never worth throwing over.
 */

export const WORKSPACE_KEY = "chimera:code:workspace";

export function readWorkspace(): string {
  try {
    return localStorage.getItem(WORKSPACE_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Persist the choice. An empty value CLEARS it rather than storing "", so "no choice" round-trips
 * as absence — otherwise a user who blanks the field would be stored as having chosen emptiness,
 * which reads identically but is a different statement. */
export function writeWorkspace(value: string): void {
  try {
    if (value) localStorage.setItem(WORKSPACE_KEY, value);
    else localStorage.removeItem(WORKSPACE_KEY);
  } catch {
    // Same reasoning as theme: the choice just will not survive a restart.
  }
}
