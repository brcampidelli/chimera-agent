import { describe, expect, it } from "vitest";
import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";

import { toCodeMirror } from "@/components/editor/diagnostics";
import type { LspDiagnostic } from "@/lib/types";

/**
 * Where a squiggle lands.
 *
 * The whole point of the server-side UTF-16 conversion is that a diagnostic on a line containing an
 * emoji points at the right characters. Nothing proves that end to end unless the browser half is
 * also checked, because the browser half is where the same conversion applied twice would undo it —
 * and it would keep passing on every ASCII file, which is most of them.
 */

function viewOf(doc: string): EditorView {
  return new EditorView({ state: EditorState.create({ doc }) });
}

function at(line: number, column: number, endColumn: number): LspDiagnostic {
  return {
    path: "a.py",
    line,
    column,
    end_line: line,
    end_column: endColumn,
    severity: "warning",
    code: "F401",
    message: "unused import",
  };
}

describe("toCodeMirror", () => {
  it("puts a squiggle on the named characters of an ascii line", () => {
    const view = viewOf("import os\n");
    const [found] = toCodeMirror(view, [at(0, 7, 9)]);

    expect(view.state.doc.sliceString(found.from, found.to)).toBe("os");
  });

  it("is not shifted by an emoji on an EARLIER line", () => {
    // CodeMirror's own offsets are UTF-16 too, so a surrogate pair further up the document must not
    // move a later line. Easy to get right, and the failure would look like "diagnostics drift the
    // further down the file you go", which reads as a server bug for a long time.
    const view = viewOf('x = "🙂"  # a comment\nimport os\n');
    const first = view.state.doc.line(1).text;
    expect(first.length).toBeGreaterThan([...first].length); // the emoji really is a surrogate pair

    const [found] = toCodeMirror(view, [{ ...at(1, 7, 9) }]);

    expect(view.state.doc.sliceString(found.from, found.to)).toBe("os");
  });

  it("underlines the text after an emoji on the SAME line", () => {
    /**
     * The case the whole conversion exists for, from the browser's side.
     *
     * The server sends UTF-16 columns; JavaScript strings ARE UTF-16; so the column is already an
     * index and must be used as one. Converting a second time here — treating it as a code point
     * offset — shifts this by one and underlines `s` where `os` belongs. It is only visible when
     * the astral character sits BEFORE the column ON ITS OWN LINE, which is why the previous test
     * is not enough on its own.
     */
    const view = viewOf('s = "🙂" ; import os\n');
    const text = view.state.doc.line(1).text;
    const column = text.indexOf("os"); // a utf-16 index, which is what the server sends
    const [found] = toCodeMirror(view, [at(0, column, column + 2)]);

    expect(view.state.doc.sliceString(found.from, found.to)).toBe("os");
    // And the naive reading — column as a code point offset — would have been wrong here, so this
    // test cannot pass by accident on an ascii line.
    expect([...text].slice(column, column + 2).join("")).not.toBe("os");
  });

  it("widens a zero-width range so the diagnostic is visible at all", () => {
    const view = viewOf("x = 1\n");
    const [found] = toCodeMirror(view, [at(0, 2, 2)]);

    expect(found.to).toBeGreaterThan(found.from);
  });

  it("clamps a line the server named but the document no longer has", () => {
    // The server can be one edit behind. Losing one squiggle is acceptable; throwing here would
    // lose every squiggle in the file, because one bad entry would take the whole batch down.
    const view = viewOf("x = 1\n");

    expect(() => toCodeMirror(view, [at(99, 0, 4)])).not.toThrow();
  });

  it("prefixes the rule code, so a warning can be looked up", () => {
    const view = viewOf("import os\n");
    const [found] = toCodeMirror(view, [at(0, 7, 9)]);

    expect(found.message).toBe("F401: unused import");
    expect(found.severity).toBe("warning");
  });

  it("falls back to an error for a severity it does not know", () => {
    // Under-reporting a problem is the worse direction: an unknown severity shown as a hint is a
    // real error rendered as a whisper.
    const view = viewOf("import os\n");
    const [found] = toCodeMirror(view, [{ ...at(0, 7, 9), severity: "banana" }]);

    expect(found.severity).toBe("error");
  });
});
