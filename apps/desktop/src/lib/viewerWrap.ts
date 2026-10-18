/** Whether the Code screen's file viewer wraps long lines.
 *
 * Prose (markdown, plain text) wrapped and everything else scrolled sideways, with no way to change
 * it: a minified JSON, a log line or a long string literal in a column a few hundred pixels wide was
 * read by dragging a horizontal scrollbar a line at a time. The default stays what it was — prose on,
 * code off, because in code a wrapped line looks like two and indentation is meaning — and the
 * viewer's toggle overrides it, remembered per kind in this webview.
 */

export type WrapKind = "prose" | "code";

export const VIEWER_WRAP_KEY = "chimera:code:viewer:wrap";

function readAll(): Partial<Record<WrapKind, boolean>> {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(VIEWER_WRAP_KEY) ?? "{}");
    if (!raw || typeof raw !== "object") return {};
    const out: Partial<Record<WrapKind, boolean>> = {};
    for (const kind of ["prose", "code"] as const) {
      const value = (raw as Record<string, unknown>)[kind];
      if (typeof value === "boolean") out[kind] = value;
    }
    return out;
  } catch {
    return {};
  }
}

/** The person's choice for this kind of file, else the default for it. */
export function readViewerWrap(kind: WrapKind): boolean {
  return readAll()[kind] ?? kind === "prose";
}

export function writeViewerWrap(kind: WrapKind, on: boolean): void {
  try {
    localStorage.setItem(VIEWER_WRAP_KEY, JSON.stringify({ ...readAll(), [kind]: on }));
  } catch {
    // The default comes back next launch: one more click, nothing lost.
  }
}
