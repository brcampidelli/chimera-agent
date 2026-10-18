/**
 * The files last opened in the Editor, per project, so an empty Editor can offer them back.
 *
 * localStorage, because it is a convenience: if storage is unavailable (private mode, a sandboxed
 * webview) the list is simply empty and nothing else is affected.
 */

const KEY = "chimera:editor:recent";
export const RECENT_LIMIT = 8;

type Store = Record<string, string[]>;

function read(): Store {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Store) : {};
  } catch {
    return {};
  }
}

export function readRecent(workspace: string | null): string[] {
  const list = read()[workspace ?? ""];
  return Array.isArray(list) ? list.filter((p): p is string => typeof p === "string") : [];
}

/** Move `path` to the front of the project's list. */
export function pushRecent(workspace: string | null, path: string): void {
  try {
    const store = read();
    const key = workspace ?? "";
    store[key] = [path, ...readRecent(workspace).filter((p) => p !== path)].slice(0, RECENT_LIMIT);
    localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    // Recents are a convenience; not worth throwing over.
  }
}
