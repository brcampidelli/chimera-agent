import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The Code screen's row has three columns and only one of them is allowed to give ground.
 *
 * A flex child defaults to `min-width: auto` and refuses to shrink below its content. Get the roles
 * wrong and the row overflows into the activity panel beside it, which does not move.
 *
 * Two rounds of this, and the second is why the check now encodes ROLES rather than a class string:
 *
 *     rc16   the inner Conversation div and the viewer got `min-w-0`; the row's own flex-1 child
 *            did not. At 1600px that was enough. At **1280** the conversation held 778px of a
 *            936px row, the viewer was crushed to 0.67px, and the pair painted 82px into the panel.
 *            Found by hit-testing a grid inside the panel: the composer strip and the transcript
 *            bubbles were what landed there — the code was innocent that time.
 *
 *     now    the sidebar is a fixed rail (`shrink-0`), the viewer is a fixed width (`shrink-0`,
 *            and `min-w-0` so its own long lines still clip), and the conversation is the one that
 *            absorbs (`flex-1 min-w-0`).
 *
 * A source check rather than a measurement, and that is a limitation worth stating: jsdom has no
 * layout engine, so a test that measured the overlap would pass on a broken build for the wrong
 * reason. What catches a NEW breaking width is driving the real app, not this file.
 */

const SRC = join(__dirname, "..", "..");

/** The line DECLARING one column: it carries the marker and it is a `className=` line.
 *
 * The `className=` half is not belt and braces. The first version matched on the marker alone and
 * found the COMMENT above the viewer — which mentions `lg:w-[28rem]` while explaining why the class
 * is there — and then reported the class as missing. That is the fourth time in this codebase a
 * check has read prose about code as code, so the rule is now written into the matcher rather than
 * left to whoever picks the next marker.
 */
function columnLine(file: string, marker: string): string {
  const source = readFileSync(join(SRC, file), "utf8");
  const line = source
    .split("\n")
    .find((l) => l.includes(marker) && l.includes("className=") && !l.trimStart().startsWith("//"));
  if (!line) throw new Error(`no className line matching ${marker} in ${file}`);
  return line;
}

const COLUMNS = [
  {
    what: "the session sidebar",
    file: join("components", "code", "SessionSidebar.tsx"),
    marker: "<aside className=",
    must: ["w-60", "shrink-0", "min-h-0"],
    mustNot: ["flex-1"],
  },
  {
    what: "the conversation — the column that absorbs",
    file: join("components", "Code.tsx"),
    marker: "<main className=",
    must: ["flex-1", "min-w-0", "min-h-0"],
    mustNot: ["shrink-0"],
  },
  {
    what: "the file viewer",
    file: join("components", "Code.tsx"),
    marker: "lg:w-[28rem]",
    must: ["min-w-0", "shrink-0", "min-h-0"],
    mustNot: ["flex-1"],
  },
  {
    what: "the conversation's inner column",
    file: join("components", "code", "Conversation.tsx"),
    marker: 'className="relative flex',
    must: ["min-w-0", "min-h-0", "flex-1"],
    mustNot: [],
  },
];

describe("the Code screen's three columns", () => {
  it.each(COLUMNS)("$what declares $must", ({ file, marker, must }) => {
    const line = columnLine(file, marker);
    for (const cls of must) expect(line, `missing ${cls}`).toContain(cls);
  });

  it.each(COLUMNS.filter((c) => c.mustNot.length))(
    "$what does not claim what belongs to another column",
    ({ file, marker, mustNot }) => {
      const line = columnLine(file, marker);
      for (const cls of mustNot) expect(line, `should not carry ${cls}`).not.toContain(cls);
    },
  );

  it("has exactly one column that grows", () => {
    // The property the whole layout rests on. Two growing columns is how the row stops having a
    // single answer to "who gives ground", and one is how the file viewer ended up at 0.67px.
    const growers = COLUMNS.filter((c) => {
      const line = columnLine(c.file, c.marker);
      return /\bflex-1\b/.test(line) && !/\bshrink-0\b/.test(line);
    });

    expect(growers.map((c) => c.what)).toEqual([
      "the conversation — the column that absorbs",
      "the conversation's inner column",
    ]);
  });
});
