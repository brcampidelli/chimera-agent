import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The three columns of the Code screen have to be able to shrink, or one paints over the panel.
 *
 * A flex child defaults to `min-width: auto`, which refuses to go below its content's intrinsic
 * width. With the file viewer open the row holds three columns, and the conversation column simply
 * would not give ground: the viewer was pushed past the row's right edge and its code painted
 * across the activity panel.
 *
 * Measured in the running app at a 1600px viewport — which is what a 2000px screenshot looks like
 * under Windows' 125% display scaling, and why it did not reproduce at first:
 *
 *     before   code ends 1511 · panel starts 1312 · overlap 199px
 *              elementFromPoint at the panel's edge returned `code.hljs`
 *     after    code ends 1301 · panel starts 1312 · gap 11px
 *              elementFromPoint returned the panel's own content
 *
 * `min-h-0` was already on both columns, guarding the vertical axis. Nothing guarded the horizontal
 * one, and the horizontal one is where a long line lives.
 *
 * This is a source check because jsdom has no layout engine: `getBoundingClientRect` returns zeros
 * there, so a test that measured the overlap would pass on a broken build for the wrong reason. It
 * asserts the declaration instead, and says so rather than pretending to measure.
 */

const SRC = join(__dirname, "..", "..");

const COLUMNS = [
  {
    file: join(SRC, "components", "code", "Conversation.tsx"),
    marker: "relative flex min-h-0",
    what: "the conversation column",
  },
  {
    file: join(SRC, "components", "Code.tsx"),
    marker: "flex min-h-0 min-w-0 flex-col border-hairline lg:w-[28rem]",
    what: "the file viewer column",
  },
];

describe("the Code screen's columns", () => {
  it.each(COLUMNS)("$what declares min-w-0", ({ file, marker }) => {
    const source = readFileSync(file, "utf8");
    const line = source.split("\n").find((l) => l.includes(marker));

    expect(line, `no element matching ${marker}`).toBeDefined();
    expect(line).toContain("min-w-0");
  });

  it("keeps min-h-0 as well — the fix adds an axis, it does not swap one", () => {
    for (const { file, marker } of COLUMNS) {
      const line = readFileSync(file, "utf8")
        .split("\n")
        .find((l) => l.includes(marker));
      expect(line).toContain("min-h-0");
    }
  });
});
