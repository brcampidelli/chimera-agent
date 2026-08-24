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

/** Rows INSIDE a column that hold controls, and must not push past it.
 *
 *  The columns guard below was written after two rounds of the row overflowing. It passed on rc18
 *  while the conversation's own header ran 95px past its column and painted "Limpar" across the file
 *  viewer's filename — 261px of buttons in a 248px column at 1280 with a viewer open.
 *
 *  A toolbar cannot both keep its buttons and fit, so the answer is `flex-wrap` rather than a clip:
 *  hiding a working control to fix a layout is trading a visible bug for an invisible one.
 */
const TOOLBAR_ROWS = [
  {
    what: "the conversation header",
    file: join("components", "code", "Conversation.tsx"),
    marker: "border-b border-hairline px-3 py-2 text-accent",
    must: ["flex-wrap"],
  },
  {
    what: "the conversation header's button group",
    file: join("components", "code", "Conversation.tsx"),
    marker: "ml-auto flex min-w-0 items-center gap-1",
    must: ["min-w-0"],
  },
];

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
    // `flex-1` WITHOUT a breakpoint would be wrong, and `flex-1` with `lg:flex-none` is the only
    // way to be right on both axes: below `lg` the row is a column and the viewer must fill the
    // height it is given and scroll inside; from `lg` up the row is a row and it must hold 28rem.
    // Written as `shrink-0` alone it grew to its content height when stacked — 2273px in a 774px
    // row — and the shell scrolled.
    must: ["min-w-0", "min-h-0", "flex-1", "lg:flex-none", "lg:shrink-0"],
    mustNot: [],
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

  it.each(TOOLBAR_ROWS)("$what can give ground rather than overflow", ({ file, marker, must }) => {
    const line = columnLine(file, marker);
    for (const cls of must) expect(line, `missing ${cls}`).toContain(cls);
  });

  it("the stacked layout gives the conversation a real share", () => {
    // Below `lg` the row is a COLUMN. Measured at 1000x900 before this rule, with a project and a
    // file open: aside 968 tall, conversation height 0 at y=1068 — off a 900px window — viewer 2273,
    // and the shell scrolled to 3341. Everything that is correct on the horizontal axis (`shrink-0`
    // on a fixed rail and a fixed panel) is wrong on the vertical one, where it reads "do not shrink
    // my height".
    //
    // Three panes do not fit: the conversation needs ~368px for its composer and header alone, and
    // capping all three to make room left it 161 and overflowing. So below `lg` there are two: the
    // capped rail, and EITHER the conversation OR the viewer.
    const rail = columnLine(join("components", "code", "SessionSidebar.tsx"), "<aside className=");
    expect(rail, "the rail must be capped while stacked").toContain("max-h-40");
    expect(rail, "and uncapped once it is a column again").toContain("lg:max-h-none");

    const source = readFileSync(join(SRC, "components", "Code.tsx"), "utf8");
    expect(
      /openFile && "max-lg:hidden"/.test(source),
      "the conversation must yield to the viewer while stacked",
    ).toBe(true);
  });

  it("has exactly one column that grows", () => {
    // The property the whole layout rests on. Two growing columns is how the row stops having a
    // single answer to "who gives ground", and one is how the file viewer ended up at 0.67px.
    const growers = COLUMNS.filter((c) => {
      const line = columnLine(c.file, c.marker);
      // Growing on the HORIZONTAL axis, which is the row from `lg` up. A `flex-1` that is
      // cancelled by `lg:flex-none` grows only while stacked, and that is a different question.
      return /flex-1/.test(line) && !/shrink-0/.test(line) || /flex-1/.test(line) && /lg:flex-none/.test(line);
    });

    expect(growers.map((c) => c.what)).toEqual([
      "the conversation — the column that absorbs",
      "the file viewer",
      "the conversation's inner column",
    ]);
  });
});
