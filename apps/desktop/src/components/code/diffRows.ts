import type { LineRef } from "@/lib/line-comments";

/** One line of a unified diff, with the file line it lands on when it is a body line. */
export interface DiffRow {
  text: string;
  /** Present only for body lines (context, added, removed): header and meta lines cannot be commented. */
  ref?: Omit<LineRef, "file" | "origin">;
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;

/** Numbers each body line of a unified diff from its hunk header.
 *
 *  Only the hunk headers are trusted for line numbers; everything else is counted. A removed line
 *  has no place on the new side, so it is anchored to the old file. Lines before the first hunk
 *  (`---`/`+++`) and the `\ No newline` marker are not body lines. */
export function parseDiffRows(patch: string): DiffRow[] {
  const rows: DiffRow[] = [];
  let inHunk = false;
  let oldLine = 0;
  let newLine = 0;
  for (const text of patch.split("\n")) {
    const hunk = HUNK.exec(text);
    if (hunk) {
      inHunk = true;
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      rows.push({ text });
      continue;
    }
    if (!inHunk || text === "" || text.startsWith("\\")) {
      rows.push({ text });
      continue;
    }
    const marker = text.startsWith("+") ? "+" : text.startsWith("-") ? "-" : " ";
    const content = text.slice(1);
    if (marker === "+") {
      rows.push({ text, ref: { side: "new", line: newLine, marker, content } });
      newLine += 1;
    } else if (marker === "-") {
      rows.push({ text, ref: { side: "old", line: oldLine, marker, content } });
      oldLine += 1;
    } else {
      rows.push({ text, ref: { side: "new", line: newLine, marker, content } });
      newLine += 1;
      oldLine += 1;
    }
  }
  return rows;
}

/** The colour a diff line takes; the same rule the renderer has always used, kept in one place. */
export function diffLineClass(text: string): string {
  if (text.startsWith("+++") || text.startsWith("---")) return "text-foreground/50";
  if (text.startsWith("@@")) return "text-accent/70";
  if (text.startsWith("+")) return "text-ok";
  if (text.startsWith("-")) return "text-bad";
  return "text-muted-foreground";
}
