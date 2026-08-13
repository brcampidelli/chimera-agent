import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";
import type { Extension } from "@codemirror/state";

/**
 * The editor, wearing the app's colours.
 *
 * CodeMirror expects a theme as a JavaScript object of CSS declarations, which is exactly the shape
 * the design gate cannot see: it scans `className` strings and whole source files for colour
 * literals, and a hex code inside a style object would sail past the className scan. So every colour
 * here is `hsl(var(--code-*))` — the tokens declared in `index.css` for both themes.
 *
 * That is not box-ticking. It is what makes the editor follow the theme toggle with no code at all:
 * flipping `data-theme` on the root re-resolves the variables, and CodeMirror never learns that
 * anything happened. A theme object full of literals would need a second object, a listener, and a
 * reconfiguration — three things that can disagree with the rest of the app.
 *
 * The one place a literal is unavoidable is `transparent`, which is not a colour.
 */

/** The chrome: the editor's own surfaces, gutter, cursor and selection. */
const chrome = EditorView.theme({
  "&": {
    color: "hsl(var(--code-plain))",
    backgroundColor: "transparent",
    height: "100%",
  },
  // The scroller owns the font, so the gutter and the content cannot drift apart — a half-pixel of
  // difference there puts every line number slightly off its line.
  ".cm-scroller": {
    fontFamily: "var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace)",
    fontSize: "13px",
    lineHeight: "1.6",
  },
  ".cm-content": { caretColor: "hsl(var(--accent))" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "hsl(var(--accent))" },
  // CodeMirror draws its own selection layer when the document has focus and the browser's native
  // one otherwise; both need saying or the selection vanishes the moment you click away.
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, .cm-content ::selection": {
    backgroundColor: "hsl(var(--code-selection))",
  },
  ".cm-activeLine": { backgroundColor: "hsl(var(--code-active-line))" },
  ".cm-gutters": {
    backgroundColor: "transparent",
    color: "hsl(var(--code-gutter))",
    borderRight: "1px solid hsl(var(--hairline))",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "hsl(var(--code-active-line))",
    color: "hsl(var(--foreground))",
  },
  ".cm-foldPlaceholder": {
    backgroundColor: "hsl(var(--surface-2))",
    border: "none",
    color: "hsl(var(--muted-foreground))",
  },
  ".cm-searchMatch": { backgroundColor: "hsl(var(--code-match))" },
  ".cm-searchMatch.cm-searchMatch-selected": { backgroundColor: "hsl(var(--code-selection))" },
  ".cm-selectionMatch": { backgroundColor: "hsl(var(--code-match))" },
  "&.cm-focused": { outline: "none" },
  ".cm-panels": {
    backgroundColor: "hsl(var(--surface-2))",
    color: "hsl(var(--foreground))",
  },
  ".cm-panels input, .cm-panels button": {
    backgroundColor: "hsl(var(--input))",
    color: "hsl(var(--foreground))",
    border: "1px solid hsl(var(--border))",
    borderRadius: "6px",
    padding: "2px 6px",
  },

  // Diagnostics. `@codemirror/lint` marks a range with a wavy underline drawn as an inline SVG
  // data URI, whose colour is baked into the image and therefore cannot follow a variable. Setting
  // `backgroundImage: none` and using `text-decoration: underline wavy` swaps a picture of a
  // squiggle for a real one — which is what lets the light and dark themes disagree about the shade
  // of red without shipping two images.
  ".cm-lintRange": { backgroundImage: "none", textDecorationSkipInk: "none" },
  ".cm-lintRange-error": { textDecoration: "underline wavy hsl(var(--bad))" },
  ".cm-lintRange-warning": { textDecoration: "underline wavy hsl(var(--warn))" },
  ".cm-lintRange-info, .cm-lintRange-hint": {
    textDecoration: "underline wavy hsl(var(--muted-foreground))",
  },
  ".cm-tooltip.cm-tooltip-lint": {
    backgroundColor: "hsl(var(--surface-2))",
    border: "1px solid hsl(var(--border))",
    borderRadius: "6px",
    color: "hsl(var(--foreground))",
  },
  ".cm-diagnostic": { padding: "4px 8px", borderLeftWidth: "3px", borderLeftStyle: "solid" },
  ".cm-diagnostic-error": { borderLeftColor: "hsl(var(--bad))" },
  ".cm-diagnostic-warning": { borderLeftColor: "hsl(var(--warn))" },
  ".cm-diagnostic-info, .cm-diagnostic-hint": { borderLeftColor: "hsl(var(--muted-foreground))" },
  ".cm-diagnosticSource": { color: "hsl(var(--muted-foreground))" },

  // The inline suggestion. `--code-comment` on purpose: it is the one colour in the palette the eye
  // already reads as "not the program", which is exactly what a proposal is. Italic would have been
  // the obvious second signal and is wrong here — the text is code, and slanting it makes the
  // indentation of a multi-line suggestion impossible to compare with the lines around it.
  ".cm-ghostText": {
    color: "hsl(var(--code-comment))",
    whiteSpace: "pre-wrap",
    opacity: "0.85",
  },
});

/**
 * The tokens.
 *
 * Lezer's tag vocabulary is large; this maps the parts that carry meaning when you are reading code
 * and lets everything else inherit `--code-plain`. Colouring twenty categories differently is how a
 * file starts to look like confetti — the point of highlighting is to make the shape of the code
 * findable at a glance, not to label every node.
 */
const highlight = HighlightStyle.define([
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], color: "hsl(var(--code-comment))", fontStyle: "italic" },
  { tag: [t.keyword, t.modifier, t.controlKeyword, t.operatorKeyword], color: "hsl(var(--code-keyword))" },
  { tag: [t.definitionKeyword, t.moduleKeyword], color: "hsl(var(--code-keyword))" },
  { tag: [t.string, t.special(t.string), t.regexp], color: "hsl(var(--code-string))" },
  { tag: [t.number, t.bool, t.null, t.atom], color: "hsl(var(--code-number))" },
  { tag: [t.function(t.variableName), t.function(t.propertyName), t.macroName], color: "hsl(var(--code-function))" },
  { tag: [t.typeName, t.className, t.namespace, t.tagName], color: "hsl(var(--code-type))" },
  { tag: [t.variableName, t.propertyName, t.attributeName], color: "hsl(var(--code-variable))" },
  { tag: [t.punctuation, t.separator, t.bracket, t.operator], color: "hsl(var(--code-punctuation))" },
  { tag: [t.invalid], color: "hsl(var(--code-invalid))" },
  // Markdown: the emphasis marks should still read as text, so only the weight changes.
  { tag: t.heading, color: "hsl(var(--code-keyword))", fontWeight: "600" },
  { tag: t.strong, fontWeight: "600" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.link, color: "hsl(var(--code-function))", textDecoration: "underline" },
]);

/** The whole look, as one extension. */
export const chimeraTheme: Extension = [chrome, syntaxHighlighting(highlight)];
