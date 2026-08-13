import { linter, type Diagnostic as CmDiagnostic } from "@codemirror/lint";
import type { Extension } from "@codemirror/state";
import type { EditorView } from "@codemirror/view";

import { getDiagnostics } from "@/lib/api";
import type { LspDiagnostic } from "@/lib/types";

/**
 * Squiggles, from a language server that agrees with CI.
 *
 * `ruff` and not a general server: it already gates every commit here, so a warning in the editor
 * corresponds to something the build will complain about. A squiggle that CI does not care about
 * teaches people to stop reading squiggles, which costs more than it gives.
 *
 * **The columns are UTF-16, and that is why this file can be short.** The protocol counts columns in
 * UTF-16 code units; JavaScript strings ARE UTF-16, so `line.slice(column)` is already right in the
 * browser and the conversion that `chimera/lsp/positions.py` performs server-side is not needed
 * here. Getting this backwards — converting again — would break exactly the files the conversion
 * exists for.
 */

/** The protocol's `(line, utf16 column)` as an absolute offset into a CodeMirror document. */
function offsetOf(view: EditorView, line: number, column: number): number {
  const doc = view.state.doc;
  // A server one edit ahead of us can name a line that no longer exists. Clamping loses one
  // squiggle; throwing loses every squiggle in the file and the panel with them.
  const lineNumber = Math.min(Math.max(line + 1, 1), doc.lines);
  const found = doc.line(lineNumber);
  return Math.min(found.from + column, found.to);
}

const SEVERITY: Record<string, CmDiagnostic["severity"]> = {
  error: "error",
  warning: "warning",
  information: "info",
  hint: "hint",
};

export function toCodeMirror(view: EditorView, found: LspDiagnostic[]): CmDiagnostic[] {
  return found.map((d) => {
    const from = offsetOf(view, d.line, d.column);
    const to = offsetOf(view, d.end_line, d.end_column);
    return {
      // A zero-width range renders as nothing at all, so a diagnostic pointing at a single position
      // is widened by one character rather than being invisible.
      from,
      to: to > from ? to : Math.min(from + 1, view.state.doc.length),
      severity: SEVERITY[d.severity] ?? "error",
      message: d.code ? `${d.code}: ${d.message}` : d.message,
      source: "ruff",
    };
  });
}

/**
 * The CodeMirror extension. Asks the server for the CURRENT buffer, debounced by the linter itself.
 *
 * `onUnavailable` is how "nothing looked" reaches the screen. Returning an empty list for a machine
 * without ruff would render a clean file, which is a claim — and the one this whole layer must not
 * make on a machine where nothing ever ran.
 */
export function diagnosticsExtension(
  workspace: string | null,
  path: () => string,
  onUnavailable: (note: string) => void,
): Extension {
  return linter(
    async (view) => {
      const file = path();
      if (!file) return [];
      if (!/\.pyi?$/i.test(file)) {
        // Nothing checks a .ts file here, so there is no request to make and no note to show. The
        // note exists to stop a file we DO check from looking clean when the check never ran; a
        // banner on every other file in the project would be tuned out within a day and would take
        // the meaningful one down with it.
        onUnavailable("");
        return [];
      }
      try {
        const result = await getDiagnostics(workspace, file, view.state.doc.toString());
        onUnavailable(result.available ? "" : result.note);
        return result.available ? toCodeMirror(view, result.diagnostics) : [];
      } catch {
        // A failed request is not a clean file either. The note says so and the squiggles stay
        // absent, which is the honest pair.
        onUnavailable("the language server could not be reached");
        return [];
      }
    },
    {
      // Long enough that typing a word is one request rather than six, short enough that the
      // squiggle arrives while the mistake is still on screen.
      delay: 600,
    },
  );
}
