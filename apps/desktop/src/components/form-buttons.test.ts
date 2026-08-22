import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * A `<button>` inside a `<form>` submits it. That is the HTML default, it is silent, and it is
 * exactly one attribute away from correct — so it comes back.
 *
 * It cost the Code screen its project: the folder picker's toggle sat in the form that opens a
 * typed path, so clicking Browse ALSO ran `switchProject(projectDraft)`. Reached the normal way,
 * with an empty field, that persisted "no project" and started a new conversation before the
 * picker had offered a single folder.
 *
 * Behavioural tests cover the one button that did it. This covers the ones nobody has written yet:
 * every button inside a form has to say which kind it is, out loud, at the call site.
 */

const SRC = join(__dirname, "..");

function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return tsxFiles(full);
    return entry.endsWith(".tsx") && !entry.includes(".test.") ? [full] : [];
  });
}

/** Comments blanked out, newlines kept so reported line numbers still point at the source.
 *
 * The first version of this check did not do it and immediately accused the comment that explains
 * the bug — prose containing "a `<button>` inside a `<form>`" was read as a form containing a
 * button, and the stray `<form` also opened a second block that double-counted the real one below
 * it. A guard that reports prose is a guard someone deletes, which leaves the actual class
 * unguarded.
 */
function withoutComments(source: string): string {
  const blank = (m: string) => m.replace(/[^\n]/g, " ");
  return source
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, blank) // {/* JSX */}
    .replace(/\/\*[\s\S]*?\*\//g, blank) // /* block */
    .replace(/(^|[^:])\/\/[^\n]*/g, (m, lead) => lead + blank(m.slice(lead.length))); // // line
}

/** Opening tags of `<button>`/`<Button>` between a `<form` and its `</form>`.
 *
 * Attribute scanning stops at the tag's own `>` — but an attribute can carry one of its own
 * (`icon={<Folder />}`), so depth is tracked rather than taking the first `>`. Getting that wrong
 * truncates the attribute list and reports a button that DOES declare its type.
 */
function buttonsInForms(input: string): { line: number; tag: string }[] {
  const source = withoutComments(input);
  const found: { line: number; tag: string }[] = [];
  const formOpen = /<form\b/g;
  let form: RegExpExecArray | null;
  while ((form = formOpen.exec(source)) !== null) {
    const close = source.indexOf("</form>", form.index);
    if (close < 0) continue;
    const block = source.slice(form.index, close);
    const button = /<(?:B|b)utton\b/g;
    let hit: RegExpExecArray | null;
    while ((hit = button.exec(block)) !== null) {
      let depth = 0;
      let end = hit.index;
      for (; end < block.length; end++) {
        const ch = block[end];
        if (ch === "{") depth++;
        else if (ch === "}") depth--;
        else if (ch === ">" && depth === 0) break;
      }
      const tag = block.slice(hit.index, end + 1);
      if (!/\btype=/.test(tag)) {
        found.push({
          line: source.slice(0, form.index + hit.index).split("\n").length,
          tag: tag.replace(/\s+/g, " ").slice(0, 90),
        });
      }
    }
  }
  return found;
}

describe("every button inside a form says what kind it is", () => {
  it("finds none that leave it to the HTML default", () => {
    const offenders = tsxFiles(SRC).flatMap((file) =>
      buttonsInForms(readFileSync(file, "utf8")).map(
        ({ line, tag }) => `${file.slice(SRC.length + 1)}:${line}  ${tag}`,
      ),
    );
    expect(offenders).toEqual([]);
  });

  it("recognises one when it is there", () => {
    // Without this the check above passes just as well when the scanner has stopped scanning —
    // an empty result reads identically whether nothing is wrong or nothing was looked at.
    const withType = `<form><Button type="button" onClick={x}>go</Button></form>`;
    const without = `<form><Button onClick={x}>go</Button></form>`;
    const withAttrCarryingAngle = `<form><Button icon={<Folder />} onClick={x}>go</Button></form>`;

    expect(buttonsInForms(withType)).toEqual([]);
    expect(buttonsInForms(without)).toHaveLength(1);
    expect(buttonsInForms(withAttrCarryingAngle)).toHaveLength(1);
  });

  it("reads code and not the prose about code", () => {
    // The exact shape that made the first version of this file wrong: a comment describing the
    // defect, sitting next to a button that is already correct.
    const commented = `<form>
      {/* A \`<button>\` inside a \`<form>\` submits it, so this one says otherwise. */}
      <Button type="button" onClick={x}>go</Button>
    </form>`;

    expect(buttonsInForms(commented)).toEqual([]);
  });
});
