import { beforeEach, describe, expect, it, vi } from "vitest";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { indentWithTab } from "@codemirror/commands";

const getInlineCompletion = vi.fn();
const postCompletionOutcome = vi.fn().mockResolvedValue({});
vi.mock("@/lib/api", () => ({
  getInlineCompletion: (...args: unknown[]) => getInlineCompletion(...args),
  postCompletionOutcome: (...args: unknown[]) => postCompletionOutcome(...args),
}));

const { inlineCompletion } = await import("@/components/editor/inline");

/**
 * The suggestion, and the two keys that answer it.
 *
 * Driven through real keydown events on the content element rather than by calling the commands,
 * because the thing most likely to be wrong is the WIRING: `indentWithTab` is in the editor's base
 * configuration and will happily eat Tab before this ever sees it. A test that called `accept()`
 * directly would pass with the precedence set wrong and the feature unusable.
 */

function mount(doc: string, at: number) {
  const view = new EditorView({
    state: EditorState.create({
      doc,
      selection: { anchor: at },
      extensions: [
        // `indentWithTab` FIRST, which is where the real editor puts it: `Editor.tsx` builds
        // `[base, extra, ...]` and CodeMirror gives earlier extensions higher precedence. Listing
        // it after the completion would be the flattering order — the extension would win by
        // position and the test would pass with `Prec.highest` deleted, proving nothing about the
        // editor anyone actually types in.
        keymap.of([indentWithTab]),
        inlineCompletion(() => "a.py", () => {}, { delay: 1 }),
      ],
    }),
    parent: document.body,
  });
  return view;
}

function press(view: EditorView, key: string, keyCode: number) {
  view.contentDOM.dispatchEvent(
    new KeyboardEvent("keydown", { key, keyCode, bubbles: true, cancelable: true }),
  );
}

/**
 * Type a character the way a person does.
 *
 * `dispatch({changes})` alone is NOT that: CodeMirror maps a position sitting exactly at an
 * insertion to BEFORE the inserted text, so the caret would stay put and the request would carry a
 * prefix missing the character that triggered it. Real input moves the caret, and getting this
 * wrong in a helper makes every assertion below off by one for a reason that has nothing to do
 * with the product.
 */
function type(view: EditorView, text: string) {
  const at = view.state.selection.main.head;
  view.dispatch({ changes: { from: at, insert: text }, selection: { anchor: at + text.length } });
}

const tick = (ms = 30) => new Promise((resolve) => setTimeout(resolve, ms));

beforeEach(() => {
  getInlineCompletion.mockReset();
  postCompletionOutcome.mockClear();
  document.body.innerHTML = "";
});

describe("inline completion", () => {
  it("shows what the model proposed as grey text, without touching the document", async () => {
    getInlineCompletion.mockResolvedValue({
      text: "return a + b",
      id: "s1",
      available: true,
      note: "",
      ms: 90,
      model: "m",
    });
    const view = mount("def add(a, b):\n    ", 19);

    type(view, "r");
    await tick();

    expect(view.dom.querySelector(".cm-ghostText")?.textContent).toBe("return a + b");
    expect(view.state.doc.toString()).toBe("def add(a, b):\n    r");
  });

  it("inserts it on Tab and records that it was taken", async () => {
    getInlineCompletion.mockResolvedValue({
      text: "eturn a + b",
      id: "s1",
      available: true,
      note: "",
      ms: 90,
      model: "m",
    });
    const view = mount("def add(a, b):\n    ", 19);
    type(view, "r");
    await tick();

    press(view, "Tab", 9);

    expect(view.state.doc.toString()).toBe("def add(a, b):\n    return a + b");
    expect(postCompletionOutcome).toHaveBeenCalledWith("s1", true);
  });

  it("leaves Tab alone when there is nothing to accept", async () => {
    // The regression that would make the editor feel broken: a completion extension that swallows
    // Tab everywhere turns indentation off for the whole file to serve a feature that is not on
    // screen.
    getInlineCompletion.mockResolvedValue({
      text: "",
      id: "",
      available: true,
      note: "",
      ms: 5,
      model: "m",
    });
    const view = mount("x = 1", 0);

    press(view, "Tab", 9);

    expect(view.state.doc.toString()).toMatch(/^\s+x = 1/);
  });

  it("drops it on Escape and records that it was refused", async () => {
    getInlineCompletion.mockResolvedValue({
      text: "  # TODO",
      id: "s2",
      available: true,
      note: "",
      ms: 70,
      model: "m",
    });
    const view = mount("x = 1", 5);
    type(view, " ");
    await tick();
    expect(view.dom.querySelector(".cm-ghostText")).not.toBeNull();

    press(view, "Escape", 27);

    expect(view.dom.querySelector(".cm-ghostText")).toBeNull();
    expect(view.state.doc.toString()).toBe("x = 1 ");
    expect(postCompletionOutcome).toHaveBeenCalledWith("s2", false);
  });

  it("does not show a suggestion for a cursor that has already moved", async () => {
    // The model answers in the time it takes to type two more characters. Placing the suggestion
    // where the caret WAS puts grey text in the middle of a word somewhere else.
    let release: (value: unknown) => void = () => {};
    getInlineCompletion.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const view = mount("x = ", 4);
    type(view, "1");
    await tick();

    view.dispatch({ selection: { anchor: 0 } });
    release({ text: "23", id: "s3", available: true, note: "", ms: 400, model: "m" });
    await tick();

    expect(view.dom.querySelector(".cm-ghostText")).toBeNull();
  });

  it("reports a standing problem, and stays quiet about an empty answer", async () => {
    // The asymmetry with diagnostics, on purpose: a missing squiggle claims the file is clean, a
    // missing suggestion claims nothing. Only the configuration problem is worth a line on screen.
    const notes: string[] = [];
    const view = new EditorView({
      state: EditorState.create({
        doc: "x = 1",
        selection: { anchor: 5 },
        extensions: [inlineCompletion(() => "a.py", (note) => notes.push(note), { delay: 1 })],
      }),
      parent: document.body,
    });

    getInlineCompletion.mockResolvedValue({
      text: "",
      id: "",
      available: false,
      note: "the model qwen is not pulled (ollama pull qwen)",
      ms: 3,
      model: "qwen",
    });
    type(view, " ");
    await tick();

    expect(notes[notes.length - 1]).toContain("ollama pull qwen");

    getInlineCompletion.mockResolvedValue({
      text: "",
      id: "",
      available: true,
      note: "",
      ms: 40,
      model: "qwen",
    });
    type(view, " ");
    await tick();

    expect(notes[notes.length - 1]).toBe("");
  });

  it("stops asking a dead port after three answers that will not change", async () => {
    // Most people who open this editor have no local model running. Retrying on every keystroke,
    // forever, is a request storm in service of an answer that is already known — and it is the
    // kind of waste that only shows up in someone else's network tab.
    getInlineCompletion.mockResolvedValue({
      text: "",
      id: "",
      available: false,
      note: "could not reach a model server at http://localhost:11434",
      ms: 2,
      model: "m",
    });
    const notes: string[] = [];
    const view = new EditorView({
      state: EditorState.create({
        doc: "",
        extensions: [inlineCompletion(() => "a.py", (note) => notes.push(note), { delay: 1 })],
      }),
      parent: document.body,
    });

    for (let i = 0; i < 8; i++) {
      type(view, "x");
      await tick(5);
    }

    expect(getInlineCompletion).toHaveBeenCalledTimes(3);
    // And it still SAYS it is off, which is the half that must not be dropped with the requests.
    expect(notes[notes.length - 1]).toContain("could not reach");
  });

  it("asks with the text on BOTH sides of the cursor", async () => {
    // Without the suffix this is autocomplete with extra steps: the model writes the closing brace
    // that is already there and redeclares the variable on the next line.
    getInlineCompletion.mockResolvedValue({
      text: "",
      id: "",
      available: true,
      note: "",
      ms: 10,
      model: "m",
    });
    const view = mount("before|after", 6);

    type(view, "X");
    await tick();

    const [prefix, suffix, key] = getInlineCompletion.mock.calls[getInlineCompletion.mock.calls.length - 1] as string[];
    expect(prefix).toBe("beforeX");
    expect(suffix).toBe("|after");
    expect(key).toBe("a.py");
  });
});
