import { useEffect, useRef } from "react";
import { EditorState, Compartment, type Extension } from "@codemirror/state";
import {
  EditorView,
  drawSelection,
  dropCursor,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  keymap,
  lineNumbers,
  rectangularSelection,
} from "@codemirror/view";
import {
  bracketMatching,
  foldGutter,
  foldKeymap,
  indentOnInput,
  indentUnit,
} from "@codemirror/language";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { highlightSelectionMatches, searchKeymap } from "@codemirror/search";

import { chimeraTheme } from "@/components/editor/theme";
import { languageFor } from "@/components/editor/languages";

/**
 * CodeMirror 6, wrapped just thinly enough for React.
 *
 * **Not a controlled component.** A `value`/`onChange` editor re-renders on every keystroke and
 * fights the editor for ownership of the document; at scale it drops characters when a render lands
 * between a keypress and the state update. The view owns the text. `doc` is the text to LOAD, read
 * when the path changes and when something outside deliberately replaces the file.
 *
 * **State is cached per path.** Switching tabs restores the cursor, the selection, the fold state
 * and the undo history of the file you left, because an `EditorState` carries all four. Remounting
 * per file (`key={path}`) would have been three lines and would throw all of it away every time you
 * glanced at another tab — which is exactly the sort of small daily loss that makes an editor feel
 * borrowed rather than built.
 */

/** Everything that does not depend on the file. Built once. */
const base: Extension = [
  lineNumbers(),
  highlightActiveLineGutter(),
  highlightSpecialChars(),
  history(),
  foldGutter(),
  drawSelection(),
  dropCursor(),
  EditorState.allowMultipleSelections.of(true),
  indentOnInput(),
  bracketMatching(),
  rectangularSelection(),
  highlightActiveLine(),
  highlightSelectionMatches(),
  // `indentWithTab` last: it must not shadow the default Tab behaviour inside a panel's input.
  keymap.of([...defaultKeymap, ...searchKeymap, ...historyKeymap, ...foldKeymap, indentWithTab]),
  EditorView.lineWrapping,
  // Four spaces because the two languages this project is written in both use them; a per-file
  // guess from the content is a P-later job, and guessing wrong reindents someone's file.
  indentUnit.of("    "),
  chimeraTheme,
];

/** Reconfigured when the file changes, so a .py tab and a .ts tab do not share a parser. */
const languageConf = new Compartment();
const readOnlyConf = new Compartment();

/**
 * Locking the document takes BOTH facets, and this is not a belt-and-braces flourish.
 *
 * `EditorState.readOnly` is what *commands* consult — Backspace, Enter, paste. It leaves the
 * content element editable, so the browser still accepts typing and CodeMirror still applies it.
 * `EditorView.editable` is what sets `contenteditable`, i.e. what stops the keystroke existing at
 * all. With only the first, a file we refuse to save because saving would truncate it was still
 * perfectly typeable — an editor that quietly discards your work is worse than one that won't take
 * it. A test asserted the lock and passed anyway, because it drove a programmatic dispatch, which
 * neither facet blocks and no user can perform.
 */
function readOnlyOf(readOnly: boolean): Extension {
  return [EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)];
}

export interface EditorProps {
  /** The file being shown. Changing it swaps the whole editor state. */
  path: string;
  /** The text to load for `path`. Ignored while that path stays open — the view owns the text. */
  doc: string;
  /** Fired on every document change, debounced by nothing: the parent decides what dirty means. */
  onChange?: (text: string) => void;
  /** Ctrl/Cmd-S. Returns nothing; the parent owns saving and its errors. */
  onSave?: () => void;
  readOnly?: boolean;
  /**
   * The live view, once. Anything that needs to reach INTO the editor — a diagnostic placing a
   * squiggle, a search hit scrolling a line into sight, a link opening at a line — needs the view
   * itself, because those are dispatches and not props. Also what lets the tests drive the editor
   * the way a person does, through the real transaction path rather than a simulated keystroke on
   * a contenteditable, which jsdom does not implement faithfully.
   */
  onReady?: (view: EditorView) => void;
  /**
   * Extra CodeMirror extensions — the diagnostics linter, today.
   *
   * Read when a document's state is BUILT, which is once per file. That is deliberate: a linter
   * rebuilt on every render would restart its debounce on every keystroke and never fire. The
   * closure inside it reads the current path, so one instance serves every tab.
   */
  extra?: Extension;
}

export function Editor({
  path,
  doc,
  onChange,
  onSave,
  onReady,
  extra,
  readOnly = false,
}: EditorProps) {
  const host = useRef<HTMLDivElement | null>(null);
  const view = useRef<EditorView | null>(null);
  // Per-path editor state, so switching tabs keeps the cursor and the undo history.
  const cache = useRef(new Map<string, EditorState>());
  // The path currently IN the view, which is not the prop until the effect below runs.
  const shown = useRef<string | null>(null);
  // Callbacks change identity every render; the view is built once, so it reads them from here
  // rather than being torn down and rebuilt to learn a new closure.
  const handlers = useRef({ onChange, onSave, onReady });
  handlers.current = { onChange, onSave, onReady };

  // Build the view once. The document is installed by the effect below, including on first run.
  useEffect(() => {
    if (!host.current) return;
    const v = new EditorView({ parent: host.current });
    view.current = v;
    handlers.current.onReady?.(v);
    return () => {
      v.destroy();
      view.current = null;
      shown.current = null;
      cache.current.clear();
    };
  }, []);

  useEffect(() => {
    const v = view.current;
    if (!v) return;
    if (shown.current === path) {
      // Same file. `doc` changing here means something outside replaced it — a save that
      // normalised the text, or a reload. Only touch the document if it actually differs, so an
      // unchanged prop cannot wipe the cursor mid-typing.
      if (v.state.doc.toString() !== doc) {
        v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: doc } });
      }
      return;
    }

    // Leaving a file: keep its state so coming back is where you left off.
    if (shown.current !== null) cache.current.set(shown.current, v.state);

    const restored = cache.current.get(path);
    v.setState(
      restored ??
        EditorState.create({
          doc,
          extensions: [
            base,
            ...(extra ? [extra] : []),
            languageConf.of(languageFor(path)),
            readOnlyConf.of(readOnlyOf(readOnly)),
            EditorView.updateListener.of((u) => {
              if (u.docChanged) handlers.current.onChange?.(u.state.doc.toString());
            }),
            keymap.of([
              {
                key: "Mod-s",
                // `preventDefault` via returning true: without it the browser's Save dialog opens
                // over the app, which in a Tauri window is a particularly confusing thing to see.
                run: () => {
                  handlers.current.onSave?.();
                  return true;
                },
              },
            ]),
          ],
        }),
    );
    shown.current = path;
  }, [path, doc, readOnly]);

  // Read-only can flip without the file changing (a run starts, the workspace goes away), and a
  // full state rebuild would lose the cursor for something that is one compartment.
  useEffect(() => {
    const v = view.current;
    if (!v || shown.current === null) return;
    v.dispatch({ effects: readOnlyConf.reconfigure(readOnlyOf(readOnly)) });
  }, [readOnly]);

  return <div ref={host} className="h-full overflow-hidden" data-testid="editor" />;
}
