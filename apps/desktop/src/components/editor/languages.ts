import type { Extension } from "@codemirror/state";
import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { python } from "@codemirror/lang-python";

/**
 * Which parser to use for a file.
 *
 * Six grammars, chosen by what this project is made of and what people bring to it — Python and
 * TypeScript for the code, JSON and Markdown for everything around it, HTML and CSS for the app.
 *
 * **Unknown extensions get no grammar rather than a guessed one.** The chat viewer
 * (`Code.tsx`) calls `hljs.highlightAuto`, which is fine for a five-line snippet in a message and
 * wrong for a file you are editing: auto-detection on a partial or unusual file mis-highlights
 * confidently, and a wrong grammar is worse than none — it colours the wrong words and folds at the
 * wrong places while looking authoritative. A plain document is honestly plain.
 *
 * These are static imports. Lazy-loading each grammar would shave the editor's chunk, at the price
 * of a state machine, an async path through mount, and a flash of unhighlighted text. It is not
 * worth it because the chunk itself is already deferred: `App` loads the whole editor screen on
 * demand, so nobody pays for a grammar until they open a file. Measured at the time of writing —
 * editor chunk 609 kB (215 kB gzipped), main bundle unchanged within 10 kB.
 */
const BY_EXT: Record<string, () => Extension> = {
  py: python,
  pyi: python,
  ts: () => javascript({ typescript: true }),
  tsx: () => javascript({ typescript: true, jsx: true }),
  mts: () => javascript({ typescript: true }),
  cts: () => javascript({ typescript: true }),
  js: javascript,
  mjs: javascript,
  cjs: javascript,
  jsx: () => javascript({ jsx: true }),
  json: json,
  jsonc: json,
  md: markdown,
  markdown: markdown,
  css: css,
  html: html,
  htm: html,
};

/** The extension of `path`, lowercased, or "" — dotfiles have none, not a name-shaped one. */
export function extensionOf(path: string): string {
  const name = path.split(/[/\\]/).pop() ?? "";
  const dot = name.lastIndexOf(".");
  // `> 0` and not `>= 0`: ".gitignore" is a name that starts with a dot, not an extension.
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** The grammar for a path, or an empty extension when nothing fits. Never guesses. */
export function languageFor(path: string): Extension {
  const make = BY_EXT[extensionOf(path)];
  return make ? make() : [];
}

/** Whether a path has a grammar — for tests and for telling someone why a file is plain. */
export function hasLanguage(path: string): boolean {
  return extensionOf(path) in BY_EXT;
}
