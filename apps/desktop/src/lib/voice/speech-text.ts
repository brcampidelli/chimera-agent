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
