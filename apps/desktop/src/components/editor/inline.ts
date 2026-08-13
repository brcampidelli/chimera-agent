import { StateEffect, StateField, Prec, type Extension } from "@codemirror/state";
import {
  Decoration,
  EditorView,
  ViewPlugin,
  WidgetType,
  keymap,
  type ViewUpdate,
} from "@codemirror/view";

import { getInlineCompletion, postCompletionOutcome } from "@/lib/api";

/**
 * The grey text ahead of the cursor.
 *
 * Three numbers decide whether this feels like help or like interference, and they are not
 * independent: **150 ms** of quiet before asking (a burst of typing is one request, not six),
 * **600 ms** of budget on the server, and **one** request in flight per file. Past roughly half a
 * second the suggestion is about text that is no longer under the cursor, so arriving late is not a
 * degraded success — it is a wrong answer delivered politely.
 *
 * **Cancellation is the feature.** An abandoned request that keeps generating occupies a local GPU
 * with text nobody will read, and the next one queues behind it; the user sees "the model is slow"
 * and goes looking in the wrong place. The `AbortController` here and the single-flight on the
 * server are two halves of one guarantee — the controller stops us waiting, the server stops the
 * model working.
 *
 * **Nothing here claims the suggestions are good.** Every one that reaches the screen is recorded,
 * and Tab and Escape are recorded against it, so an acceptance rate exists to be looked at before
 * anybody writes a sentence about quality.
 */

const setSuggestion = StateEffect.define<Suggestion | null>();

interface Suggestion {
  text: string;
  /** Where it belongs. A suggestion is void the moment the cursor is elsewhere. */
  at: number;
  /** The server's id for it, so Tab and Escape land in the same ledger row. */
  id: string;
}

class GhostWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }

  eq(other: GhostWidget): boolean {
    return other.text === this.text;
  }

  toDOM(): HTMLElement {
    const span = document.createElement("span");
    span.className = "cm-ghostText";
    span.textContent = this.text;
    // Not focusable, not selectable, invisible to a screen reader's document flow: it is a proposal,
    // not content. A copy that silently included text the user never accepted would be worse than
    // no completion at all.
    span.setAttribute("aria-hidden", "true");
    return span;
  }

  ignoreEvent(): boolean {
    return false;
  }
}

const suggestion = StateField.define<Suggestion | null>({
  create: () => null,
  update(value, tr) {
    for (const effect of tr.effects) if (effect.is(setSuggestion)) return effect.value;
    // Any edit invalidates it. Mapping the position through the change instead would keep grey text
    // alive across an edit that may have made it nonsense — the request that replaces it is already
    // on its way.
    if (tr.docChanged) return null;
    if (value && tr.selection && tr.selection.main.head !== value.at) return null;
    return value;
  },
  provide: (field) =>
    EditorView.decorations.from(field, (value) =>
      value
        ? Decoration.set([
            Decoration.widget({ widget: new GhostWidget(value.text), side: 1 }).range(value.at),
          ])
        : Decoration.none,
    ),
});

/** Accept: insert the text and move the cursor to its end. */
function accept(view: EditorView): boolean {
  const current = view.state.field(suggestion, false);
  if (!current) return false;
  view.dispatch({
    changes: { from: current.at, insert: current.text },
    selection: { anchor: current.at + current.text.length },
    effects: setSuggestion.of(null),
  });
  void report(current.id, true);
  return true;
}

function dismiss(view: EditorView): boolean {
  const current = view.state.field(suggestion, false);
  if (!current) return false;
  view.dispatch({ effects: setSuggestion.of(null) });
  void report(current.id, false);
  return true;
}

/** Ids already answered for, so a dismissal followed by a re-render cannot count twice. */
const reported = new Set<string>();

async function report(id: string, accepted: boolean): Promise<void> {
  if (!id || reported.has(id)) return;
  reported.add(id);
  if (reported.size > 500) reported.clear();
  try {
    await postCompletionOutcome(id, accepted);
  } catch {
    // A lost sample is a lost sample. Surfacing it would put an error on screen about a statistic.
  }
}

export function inlineCompletion(
  key: () => string,
  onUnavailable: (note: string) => void,
  options: { delay?: number } = {},
): Extension {
  const delay = options.delay ?? 150;

  const plugin = ViewPlugin.fromClass(
    class {
      private timer: ReturnType<typeof setTimeout> | null = null;
      private abort: AbortController | null = null;
      /**
       * Consecutive standing failures. Most people who open this editor will not have a local model
       * running, and asking a dead port on every keystroke forever is a request storm in service of
       * an answer that will not change. After three it stops asking; the note stays on screen, so
       * the feature is off and SAYS it is off, which is the pair that matters.
       */
      private strikes = 0;

      constructor(readonly view: EditorView) {}

      update(update: ViewUpdate) {
        if (!update.docChanged) return;
        if (this.strikes >= 3) return;
        // A change that came from accepting a suggestion must not immediately ask for another; that
        // is how a Tab turns into a paragraph nobody asked for.
        if (update.transactions.some((tr) => tr.effects.some((e) => e.is(setSuggestion)))) return;
        this.schedule();
      }

      schedule() {
        this.cancel();
        this.timer = setTimeout(() => void this.ask(), delay);
      }

      cancel() {
        if (this.timer !== null) clearTimeout(this.timer);
        this.timer = null;
        // Aborting is what stops us waiting; the server's single-flight is what stops the model.
        this.abort?.abort();
        this.abort = null;
      }

      async ask() {
        const view = this.view;
        const range = view.state.selection.main;
        if (!range.empty || view.state.readOnly) return;
        const at = range.head;
        const doc = view.state.doc;
        const controller = new AbortController();
        this.abort = controller;
        try {
          const found = await getInlineCompletion(
            doc.sliceString(0, at),
            doc.sliceString(at),
            key(),
            controller.signal,
          );
          this.strikes = found.available ? 0 : this.strikes + 1;
          onUnavailable(found.available ? "" : found.note);
          if (!found.text || controller.signal.aborted) return;
          // The cursor may have moved while the model was thinking. Showing the suggestion at its
          // old position would put grey text in the middle of a word somewhere else.
          if (view.state.selection.main.head !== at) return;
          view.dispatch({ effects: setSuggestion.of({ text: found.text, at, id: found.id }) });
        } catch {
          // An aborted fetch throws, and so does a server that is not there. Neither is worth a
          // banner: an absent suggestion, unlike an absent diagnostic, asserts nothing about the
          // file. Only a standing configuration problem gets the note above.
        }
      }

      destroy() {
        this.cancel();
      }
    },
  );

  return [
    suggestion,
    plugin,
    // Highest precedence, or `indentWithTab` in the base configuration swallows Tab first and the
    // suggestion can only ever be dismissed. Both handlers return false when there is nothing to
    // accept, so indentation still works exactly as before whenever no suggestion is showing.
    Prec.highest(
      keymap.of([
        { key: "Tab", run: accept },
        { key: "Escape", run: dismiss },
      ]),
    ),
  ];
}

export const _internals = { suggestion, setSuggestion, accept, dismiss };
