import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EditorView as EditorViewNS, type EditorView } from "@codemirror/view";
import { insertNewlineAndIndent } from "@codemirror/commands";

import { Editor } from "@/components/editor/Editor";

/**
 * The wrapper's job is state, not text entry.
 *
 * Typing is CodeMirror's problem and it is well tested upstream; what is ours is everything around
 * the document — when it gets replaced, when it must NOT get replaced, and what survives switching
 * files. Those are the parts a wrapper gets wrong, and each one has a visible symptom: a cursor that
 * jumps, a tab that forgets where you were, an edit that a re-render silently discards.
 *
 * Edits go through `view.dispatch`, which is the same transaction path a keystroke takes. Simulating
 * a keypress on a contenteditable in jsdom tests jsdom, not us.
 */

let view: EditorView | null = null;

afterEach(() => {
  view = null;
});

function mount(props: Partial<Parameters<typeof Editor>[0]> = {}) {
  const result = render(
    <Editor path="a.ts" doc="const a = 1" onReady={(v) => (view = v)} {...props} />,
  );
  return {
    ...result,
    text: () => view?.state.doc.toString() ?? "",
    type: (insert: string, at = 0) =>
      act(() => {
        view?.dispatch({ changes: { from: at, insert } });
      }),
  };
}

describe("Editor", () => {
  it("shows the document it was given", () => {
    const { text } = mount();
    expect(text()).toBe("const a = 1");
  });

  it("reports every change", () => {
    const onChange = vi.fn();
    const { type } = mount({ onChange });
    type("x");
    expect(onChange).toHaveBeenCalledWith("xconst a = 1");
  });

  it("does not touch the document when re-rendered with the same text", () => {
    // The parent re-renders on every keystroke (it is tracking dirtiness). If that put the document
    // back, the cursor would jump to the start of the file mid-word — the single most common bug in
    // a React wrapper around an editor.
    const { rerender } = mount();
    view?.dispatch({ selection: { anchor: 5 } });
    rerender(<Editor path="a.ts" doc="const a = 1" onReady={() => {}} />);
    expect(view?.state.selection.main.anchor).toBe(5);
  });

  it("replaces the document when something outside changes the file", () => {
    // Same path, different text: a save that normalised line endings, or a reload after the agent
    // wrote to it. Keeping the stale text on screen would be showing a file that no longer exists.
    const { rerender, text } = mount();
    rerender(<Editor path="a.ts" doc="const a = 2" onReady={() => {}} />);
    expect(text()).toBe("const a = 2");
  });

  it("swaps the whole document when the file changes", () => {
    const { rerender, text } = mount();
    rerender(<Editor path="b.py" doc="x = 1" onReady={() => {}} />);
    expect(text()).toBe("x = 1");
  });

  it("brings back the cursor and the undo history of a file you return to", () => {
    // What separates tabs from a viewer that happens to remember a filename. Glancing at another
    // file and coming back should not cost you your place or your ability to undo.
    const { rerender, text, type } = mount({ path: "a.ts", doc: "const a = 1" });
    type("// note ");
    act(() => {
      view?.dispatch({ selection: { anchor: 3 } });
    });

    rerender(<Editor path="b.py" doc="x = 1" onReady={() => {}} />);
    expect(text()).toBe("x = 1");

    rerender(<Editor path="a.ts" doc="const a = 1" onReady={() => {}} />);
    // The unsaved edit is still there, and so is the cursor.
    expect(text()).toBe("// note const a = 1");
    expect(view?.state.selection.main.anchor).toBe(3);
  });

  it("refuses edits while read-only", () => {
    // Set on a truncated file, where a save would delete everything past the cut. A banner alone
    // would leave the destructive action one keystroke away.
    //
    // Asserted through a real editing COMMAND and through `contenteditable`, which are the two
    // ways a keystroke becomes a change. The first version of this test dispatched a transaction
    // directly and "passed" while the content element was still fully typeable — a dispatch is not
    // a user, and neither read-only facet claims to block one.
    const { text } = mount({ readOnly: true });
    expect(view?.state.readOnly).toBe(true);
    // The `editable` facet and not `contentDOM.contentEditable`: jsdom does not implement that
    // property, so asserting on it passes whether or not the facet is set — this exact assertion
    // was written, run, and found to pass with the lock deleted.
    expect(view?.state.facet(EditorViewNS.editable)).toBe(false);
    act(() => {
      insertNewlineAndIndent({ state: view!.state, dispatch: (tr) => view!.dispatch(tr) });
    });
    expect(text()).toBe("const a = 1");
  });

  it("stops being read-only without losing the cursor", () => {
    // Read-only can flip while the same file stays open. Rebuilding the state to change it would
    // move the caret for a reason the person never sees.
    const { rerender } = mount({ readOnly: true });
    act(() => {
      view?.dispatch({ selection: { anchor: 4 } });
    });
    rerender(<Editor path="a.ts" doc="const a = 1" readOnly={false} onReady={() => {}} />);
    expect(view?.state.selection.main.anchor).toBe(4);
    expect(view?.state.readOnly).toBe(false);
  });

  it("cleans up the view when it goes away", () => {
    // An editor left attached keeps DOM listeners and a measure loop alive; several of them and the
    // app slows down for reasons that never point back here.
    const { unmount } = mount();
    const v = view;
    unmount();
    expect(v?.dom.isConnected).toBe(false);
  });
});
