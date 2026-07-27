/**
 * The design system's enforcement gate.
 *
 * `DESIGN.md` states the rules; this file is what makes them true. Without it a guideline document
 * is a wish — this repo already proved that, having carried a coherent design intent in a six-line
 * CSS comment while 150-odd arbitrary values accumulated around it.
 *
 * It runs inside the existing Vitest step, so it needs no ESLint (this repo has none), no new
 * dependency, and no CI change.
 *
 * Two kinds of rule live here:
 *   - **Absolute** — a violation fails, full stop. Reserved for things with zero current violations,
 *     so they can never regress.
 *   - **Ratchet** — a counted violation that may not INCREASE (see ratchet.json). This is what lets
 *     the gate land today instead of after a refactor of every file.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import ratchet from "./ratchet.json";

// Source files come through Vite's glob, which returns their text verbatim.
const sources = import.meta.glob("../**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/**
 * Stylesheets are read from disk, NOT through `import.meta.glob`.
 *
 * Vitest stubs CSS imports to an empty string by default (`test.css` is false), and `?raw` does not
 * exempt them. Every CSS rule below therefore ran against `""` and passed vacuously — a keyframe
 * with no reduced-motion answer sailed through, which is precisely the check this file exists for.
 * A gate that cannot fail is worse than no gate, because it manufactures confidence.
 */
function readStyles(dir: string, out: Record<string, string> = {}): Record<string, string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) readStyles(path, out);
    else if (entry.name.endsWith(".css")) out[path] = readFileSync(path, "utf8");
  }
  return out;
}

// Vitest's root is apps/desktop (vite.config.ts lives there), so cwd is stable.
const styles = readStyles(join(process.cwd(), "src"));

/** Source files excluding this gate and its data, which necessarily contain the banned patterns. */
function appSources(): [string, string][] {
  return Object.entries(sources).filter(([path]) => !path.includes("/design/"));
}

/** Every `className="…"` / `className={"…"}` string literal in the app, with its file. */
function classNameLiterals(): { file: string; value: string }[] {
  const out: { file: string; value: string }[] = [];
  for (const [file, text] of appSources()) {
    for (const m of text.matchAll(/className=(?:"([^"]*)"|\{`([^`]*)`\}|\{"([^"]*)"\})/g)) {
      out.push({ file, value: m[1] ?? m[2] ?? m[3] ?? "" });
    }
  }
  return out;
}

function countMatches(pattern: RegExp): { total: number; where: string[] } {
  const where: string[] = [];
  let total = 0;
  for (const [file, text] of appSources()) {
    const hits = text.match(pattern);
    if (hits) {
      total += hits.length;
      where.push(`${file} (${hits.length})`);
    }
  }
  return { total, where };
}

describe("typography", () => {
  it("uses the named scale, never a pixel value", () => {
    // xs 11 · sm 13 · base 15 · lg 18 · xl 22 (tailwind.config.js). Five names cover the whole app;
    // a sixth size is a design decision, not something to smuggle in as an arbitrary utility.
    const { total, where } = countMatches(/\btext-\[\d+px\]/g);
    expect(total, `arbitrary font sizes in: ${where.join(", ")}`).toBe(0);
  });
});

describe("colour", () => {
  it("never hard-codes a colour anywhere in a source file", () => {
    // A literal colour cannot respond to the theme: `ui/panel.tsx` carried an inline amber for the
    // warn badge that had no light-palette counterpart at all.
    //
    // This scans whole files rather than just `className` strings, and deliberately so — that badge
    // lived in a variant MAP, so a className-only rule would have walked straight past the very
    // violation that motivated the `--warn` token.
    //
    // `hsl(var(--x))` is allowed: that IS the token, reached through the arbitrary-value syntax for
    // the CSS properties Tailwind doesn't expose (accent-color, and friends).
    const offenders: string[] = [];
    for (const [file, text] of appSources()) {
      for (const m of text.matchAll(/hsl\([^)]*\)|#[0-9a-fA-F]{6}\b/g)) {
        if (m[0].includes("var(--")) continue;
        offenders.push(`${file}: ${m[0]}`);
      }
    }
    expect(offenders, "colour literals").toEqual([]);
  });

  it("never separates surfaces with raw white or black alpha", () => {
    // `border-white/5` is invisible on a white card. Every one of these became `--hairline`,
    // `--surface-2` or `--surface-hover`, each of which has a real light-theme value.
    const { total, where } = countMatches(/\b(?:border|divide|ring|bg)-(?:white|black)\/\[?[\d.]+\]?/g);
    expect(total, `raw alpha separators in: ${where.join(", ")}`).toBe(0);
  });
});

describe("motion", () => {
  it("never hard-codes a duration or an easing curve", () => {
    // Durations are --dur-1..4 and easings are --ease-*. An app where every animation picks its own
    // timing has no rhythm, and the difference is felt long before it is noticed.
    const { total, where } = countMatches(/\b(?:duration|ease|delay|transition)-\[[^\]]+\]/g);
    expect(total, `raw motion values in: ${where.join(", ")}`).toBe(0);
  });

  it("declares a duration and an easing wherever it declares a transition", () => {
    // Ratcheted: 17 sites inherited Tailwind's implicit 150ms/ease-in-out at the time this landed.
    // They are not wrong, they are unstated — and unstated timing is how the rhythm drifts.
    const untokened = classNameLiterals().filter(({ value }) => {
      const words = value.split(/\s+/);
      const transitions = words.filter((w) => /(^|:)transition(-|$)/.test(w));
      if (transitions.length === 0) return false;
      return !words.some((w) => /(^|:)duration-/.test(w)) || !words.some((w) => /(^|:)ease-/.test(w));
    });
    expect(untokened.length).toBeLessThanOrEqual(ratchet.transitionsWithoutTokens);
  });

  it("is actually reading the stylesheets", () => {
    // The rule below was silently vacuous once already: Vitest handed it empty strings and it
    // "passed" over a keyframe with no reduced-motion answer. Assert the input is real before
    // trusting any verdict drawn from it.
    const all = Object.values(styles).join("");
    expect(Object.keys(styles).length).toBeGreaterThan(0);
    expect(all.length).toBeGreaterThan(1000);
    expect(all).toContain("@keyframes");
  });

  it("pairs every keyframe with a reduced-motion answer", () => {
    // The structural rule. A keyframe that nobody thought about under reduced motion is exactly the
    // one that will spin forever for the person who asked their OS for calm. If this fails, the fix
    // is to handle the animation in the reduced-motion block — not to delete the assertion.
    for (const [file, css] of Object.entries(styles)) {
      const names = [...css.matchAll(/@keyframes\s+([\w-]+)/g)].map((m) => m[1]);
      if (names.length === 0) continue;
      const reducedBlocks = css.match(
        /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?\n\}|\[data-motion=["']reduced["']\][\s\S]*?\n\}/g,
      );
      expect(reducedBlocks, `${file} defines keyframes but no reduced-motion block`).toBeTruthy();
      const reduced = (reducedBlocks ?? []).join("\n");
      // A blanket `animation-duration: 1ms` selector covers every keyframe at once, which is the
      // recommended shape — accept it instead of demanding each name be listed.
      const blanket = /\*[^{]*\{[^}]*animation-duration:\s*1ms/.test(reduced);
      if (blanket) continue;
      for (const name of names) {
        expect(reduced, `${file}: keyframe "${name}" has no reduced-motion handling`).toContain(name);
      }
    }
  });
});

describe("accessibility", () => {
  it("keeps a single shared focus ring rather than growing per-component ones", () => {
    // One definition means one place to fix it. Ratcheted from 1 (ui/button.tsx) — the rail and the
    // primitives will import it rather than each inventing a ring.
    const { total } = countMatches(/focus-visible:/g);
    expect(total).toBeGreaterThanOrEqual(ratchet.focusVisibleRules);
  });
});

describe("the ratchet", () => {
  it("does not let arbitrary utilities grow", () => {
    // The catch-all: any `utility-[value]` that escaped the specific rules above. It may fall; it
    // may not rise. Whoever needs a new one has to lower a number in ratchet.json to pay for it.
    const { total, where } = countMatches(
      /\b(?:w|h|min-w|min-h|max-w|max-h|p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|top|left|right|bottom|z|opacity|shadow|leading|tracking)-\[[^\]]+\]/g,
    );
    expect(total, `arbitrary utilities in: ${where.join(", ")}`).toBeLessThanOrEqual(
      ratchet.arbitraryUtilities,
    );
  });
});
