/**
 * What an answer sounds like: the Markdown an agent writes, reduced to sentences a voice can read.
 *
 * A code block read aloud character by character is noise that lasts a minute; a link's URL is the
 * same. So fenced code is replaced by a short spoken marker, inline code keeps its text (a file name
 * or a flag is worth hearing), links keep their label, and the emphasis and heading marks that only
 * mean something on a screen are dropped. Deterministic and small on purpose — the transcript on
 * screen is the record, this is the reading of it.
 */

/** Spoken in place of a fenced code block, in whatever language the app is showing. */
export function plainForSpeech(markdown: string, codeMarker = "(code)"): string {
  let text = markdown.replace(/\r\n/g, "\n");
  // Fenced code first, so nothing inside it is treated as Markdown.
  text = text.replace(/```[\s\S]*?```/g, ` ${codeMarker} `);
  text = text.replace(/~~~[\s\S]*?~~~/g, ` ${codeMarker} `);
  // Images: the alt text is the only spoken part; a picture has no sound.
  text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
  // Links: the label, never the URL.
  text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
  // Bare URLs: say the host, not the path.
  text = text.replace(/https?:\/\/([^\s/)]+)[^\s)]*/g, "$1");
  // Inline code keeps its content.
  text = text.replace(/`([^`\n]+)`/g, "$1");
  // Headings, quotes, list markers, tables, rules, emphasis.
  text = text.replace(/^\s{0,3}#{1,6}\s+/gm, "");
  text = text.replace(/^\s*>\s?/gm, "");
  text = text.replace(/^\s*[-*+]\s+/gm, "");
  text = text.replace(/^\s*\d+\.\s+/gm, "");
  text = text.replace(/^\s*\|?[-:| ]+\|?\s*$/gm, "");
  text = text.replace(/\|/g, ", ");
  text = text.replace(/^\s*([-*_]\s*){3,}$/gm, "");
  text = text.replace(/(\*\*|__)(.*?)\1/g, "$2");
  text = text.replace(/(\*|_)(?=\S)(.*?)(?<=\S)\1/g, "$2");
  text = text.replace(/~~(.*?)~~/g, "$1");
  // Whitespace: paragraphs become pauses, runs of spaces collapse.
  text = text.replace(/[ \t]+/g, " ");
  text = text.replace(/\n{2,}/g, ". ");
  text = text.replace(/\n/g, " ");
  text = text.replace(/\s*\.\s*\./g, ".");
  return text.replace(/\s+/g, " ").trim();
}

/** The BCP-47 tag a synthesis voice is chosen by, from the app's two-letter language. */
export function speechLocale(lang: string): string {
  const table: Record<string, string> = {
    en: "en-US",
    pt: "pt-BR",
    es: "es-ES",
    fr: "fr-FR",
    de: "de-DE",
    it: "it-IT",
    pl: "pl-PL",
    zh: "zh-CN",
    ja: "ja-JP",
    ru: "ru-RU",
  };
  return table[lang] ?? "en-US";
}

/** The line the model was asked to put between the spoken part of an answer and the part for the
 *  screen — a Markdown rule, `---` alone on a line (`***` and `___` read the same). The index of
 *  that line, or -1. */
export function screenPartStart(raw: string): number {
  const rule = /^[ \t]{0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/m;
  const match = rule.exec(raw);
  return match ? match.index : -1;
}

/**
 * Where a growing answer can be cut so that everything before the cut can be read now: after a
 * sentence end (`.`, `!`, `?`, `…`, then any closing marks, then whitespace) or a line end —
 * never inside an open code fence, and never right after a list number alone on its line. The
 * last such point at or after `from`, or `from` when nothing is ready yet.
 *
 * Reading as the answer streams is what makes the first sentence audible a second or two after
 * the model starts, instead of after it stops: at twenty tokens a second, a three-hundred-token
 * answer is fifteen seconds of silence before the first word. Cutting at sentence and line ends
 * keeps `plainForSpeech` sound on each piece — a heading, a list item and a link never straddle a
 * cut, and a fenced block is held whole until it closes so its marker is spoken once.
 */
export function readyCut(raw: string, from: number): number {
  let cut = from;
  let fenceOpen = false;
  let lineStart = 0;
  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (i === lineStart && /^[ 	]{0,3}```/.test(raw.slice(i, i + 6))) fenceOpen = !fenceOpen;
    if (ch === "\n") {
      lineStart = i + 1;
      if (!fenceOpen && i + 1 > from) cut = i + 1;
      continue;
    }
    if (fenceOpen || i < from) continue;
    if (ch === "." || ch === "!" || ch === "?" || ch === "…") {
      // "1." at the start of a line is a list marker, not the end of a sentence.
      if (ch === "." && /^\s*\d+$/.test(raw.slice(lineStart, i))) continue;
      let j = i + 1;
      while (j < raw.length && /[*_)"'”»]/.test(raw[j])) j++;
      if (j < raw.length && /\s/.test(raw[j]) && j > from) cut = j;
    }
  }
  return cut;
}

/** How many sentences a piece of spoken text holds — at least one, if it holds anything. */
export function countSentences(plain: string): number {
  if (!plain.trim()) return 0;
  return Math.max(1, (plain.match(/[.!?…](?:\s|$)/g) ?? []).length);
}

/** The first `n` sentences of a piece of spoken text, and whether anything was left behind. */
export function firstSentences(plain: string, n: number): { kept: string; truncated: boolean } {
  if (n <= 0) return { kept: "", truncated: plain.trim() !== "" };
  const parts = plain.split(/(?<=[.!?…])\s+/);
  if (parts.length <= n) return { kept: plain, truncated: false };
  return { kept: parts.slice(0, n).join(" "), truncated: parts.slice(n).join(" ").trim() !== "" };
}
