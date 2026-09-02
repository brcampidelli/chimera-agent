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
function readStyles(
  dir: string,
  out: Record<string, string> = {},
): Record<string, string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) readStyles(path, out);
    else if (entry.name.endsWith(".css"))
      out[path] = readFileSync(path, "utf8");
  }
  return out;
}

// Vitest's root is apps/desktop (vite.config.ts lives there), so cwd is stable.
const styles = readStyles(join(process.cwd(), "src"));

/** Source files excluding this gate and its data, which necessarily contain the banned patterns. */
function appSources(): [string, string][] {
  // What SHIPS. Two exclusions, and the first one was silently doing nothing.
  //
  // The keys `import.meta.glob` hands back are relative to THIS file, so a sibling in `src/design/`
  // arrives as `./thing.ts` and a component as `../components/Thing.tsx`. Neither contains
  // "/design/", so the old filter matched no file at all — it read as an exclusion and was a no-op.
  // A guard on this folder's own files is the one thing it was written to prevent, and it took a
  // new sibling that talks ABOUT colour to notice.
  //
  // Test files go too, for the reason that surfaced it: a test that asserts a literal colour is
  // wrong has to contain that literal, and flagging it is the same class of mistake as reading a
  // comment about `hsl()` as a call to it.
  return Object.entries(sources).filter(
    ([path]) => !path.startsWith("./") && !/\.test\.tsx?$/.test(path),
  );
}

/** Every `className="…"` / `className={"…"}` string literal in the app, with its file. */
function classNameLiterals(): { file: string; value: string }[] {
  const out: { file: string; value: string }[] = [];
  for (const [file, text] of appSources()) {
    for (const m of text.matchAll(
      /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{"([^"]*)"\})/g,
    )) {
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

  it("uses the ink pair whenever a state colour is a TEXT colour", () => {
    // `--ok` and `--bad` are fills. Read as 11px text on the light theme they measure 3.06:1 and
    // 3.82:1, under the 4.5 small text needs — which is why each has a darker `-foreground` pair.
    //
    // The rule is mechanical because the distinction is: a className carrying a font size is
    // styling TEXT, and one that only carries `h-4 w-4` is styling an icon, where the bar is 3:1
    // and the plain token is right. 58 sites were on the wrong side of that line, spread over 28
    // files, and every one of them looked correct beside a badge that had already been fixed.
    // Split on spaces instead of matching with word boundaries. The first version used regexes
    // and the escape did not survive being written to disk: `size` ended up as a BACKSPACE
    // character followed by `text-`, which matches nothing, so the check skipped every value and
    // passed while verifying nothing at all. Sabotage is the only reason that surfaced. Tailwind
    // class lists are space-separated, so exact membership says the same thing and cannot rot.
    const SIZES = new Set(["text-xs", "text-sm", "text-base", "text-lg", "text-xl"]);
    // `text-accent` joined late, and the reason is worth keeping: `--accent-foreground` already
    // existed, so it LOOKED like the accent had its ink pair. It is white — for text ON a solid
    // accent fill, the opposite direction. Text IN the accent colour had no token at all and
    // measured 4.29:1 on the page, 3.29:1 on the tint a selected toggle paints itself with.
    const STATES = new Set(["text-ok", "text-bad", "text-warn", "text-accent"]);
    const offenders: string[] = [];
    // Two ways to be sure a token is colouring TEXT, because the font size is not always in the
    // same string. `panel.tsx` keeps its tones in a map — `bg-ok/15 text-ok ring-1 ring-ok/20` —
    // while the `text-xs` that makes it text lives on the element. The size rule walked straight
    // past it, which is how the badge tones were wrong to begin with, and sabotage proved the
    // first version of THIS guard walked past it too.
    //
    // So: a size class in the same string, or a fill of the same colour in the same string. The
    // second is the badge shape and means a label on its own wash by construction.
    const ink = (c: string) => (c === "text-accent" ? "text-accent-ink" : `${c}-foreground`);
    // EVERY quoted string, not just `className=` ones. `panel.tsx` keeps its badge tones in a
    // variant map, so a className-only scan walks past them — which is the exact blind spot the
    // colour-literal test above documents having been written to avoid, and which this guard
    // inherited by reusing `classNameLiterals()`. Sabotage on two different tones proved it.
    // Every quoted string, and every `cn(...)` call read as ONE class list.
    //
    // Three shapes had to be covered, each found only after the previous rule shipped:
    //   1. size and colour in the same string   -> the original rule
    //   2. a variant MAP, no size anywhere near -> caught by pairing `bg-x/NN` with `text-x`
    //   3. size and colour in different ARMS of one `cn()` — `cn("... text-xs", on ? "text-accent"
    //      : "...")`. Neither earlier rule sees it, and it is what left the project name in the
    //      session sidebar at 4.29:1 after two rounds of sweeping.
    const strings: { file: string; value: string }[] = [];
    const QUOTE = String.fromCharCode(34);
    for (const [file, text] of appSources()) {
      const chunks = text.split(QUOTE);
      for (let i = 1; i < chunks.length; i += 2) strings.push({ file, value: chunks[i] });
      // `cn(` spans, joined. Depth counting rather than a regex: a call can nest, and the arms
      // are separated by commas that a flat match would not respect.
      let at = text.indexOf("cn(");
      while (at !== -1) {
        let depth = 0;
        let end = at + 2;
        for (; end < text.length; end++) {
          if (text[end] === "(") depth++;
          else if (text[end] === ")") { depth--; if (depth === 0) break; }
        }
        const inner = text.slice(at, end).split(QUOTE);
        const joined = inner.filter((_, i) => i % 2 === 1).join(" ");
        if (joined) strings.push({ file, value: joined });
        at = text.indexOf("cn(", end);
      }
    }
    for (const { file, value } of strings) {
      const parts = value.split(" ").map((c) => c.trim()).filter(Boolean);
      const hit = parts.find((c) => STATES.has(c));
      if (!hit) continue;
      const fill = hit.replace("text-", "bg-");
      const isText =
        parts.some((c) => SIZES.has(c)) || parts.some((c) => c.startsWith(`${fill}/`));
      if (isText) offenders.push(`${file}: ${hit} - use ${ink(hit)}`);
    }
    expect(offenders, "state colours used as text").toEqual([]);
  });

  it("never separates surfaces with raw white or black alpha", () => {
    // `border-white/5` is invisible on a white card. Every one of these became `--hairline`,
    // `--surface-2` or `--surface-hover`, each of which has a real light-theme value.
    const { total, where } = countMatches(
      /\b(?:border|divide|ring|bg)-(?:white|black)\/\[?[\d.]+\]?/g,
    );
    expect(total, `raw alpha separators in: ${where.join(", ")}`).toBe(0);
  });
});

describe("motion", () => {
  it("never hard-codes a duration or an easing curve", () => {
    // Durations are --dur-1..4 and easings are --ease-*. An app where every animation picks its own
    // timing has no rhythm, and the difference is felt long before it is noticed.
    const { total, where } = countMatches(
      /\b(?:duration|ease|delay|transition)-\[[^\]]+\]/g,
    );
    expect(total, `raw motion values in: ${where.join(", ")}`).toBe(0);
  });

  it("declares a duration and an easing wherever it declares a transition", () => {
    // Ratcheted: 17 sites inherited Tailwind's implicit 150ms/ease-in-out at the time this landed.
    // They are not wrong, they are unstated — and unstated timing is how the rhythm drifts.
    const untokened = classNameLiterals().filter(({ value }) => {
      const words = value.split(/\s+/);
      const transitions = words.filter((w) => /(^|:)transition(-|$)/.test(w));
      if (transitions.length === 0) return false;
      return (
        !words.some((w) => /(^|:)duration-/.test(w)) ||
        !words.some((w) => /(^|:)ease-/.test(w))
      );
    });
    expect(untokened.length).toBeLessThanOrEqual(
      ratchet.transitionsWithoutTokens,
    );
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

  it("never animates transform on an overlay that centres itself with one", () => {
    // How the model dialog lost its footer, and the command palette drifted half a width to the
    // right: `overlay-in` ended on `transform: none` with `animation-fill-mode: both`, so the fill
    // kept overwriting the `-translate-x-1/2 -translate-y-1/2` that does the centring — for as long
    // as the element existed, not just while it played. The dialog was then drawn with its top-left
    // corner at the middle of the window, half of it below the bottom edge, with the action in the
    // half nobody could reach. Animate the individual `scale` property instead: it composes with
    // `transform` rather than replacing it.
    const css = Object.values(styles).join("\n");

    // class → the animations CSS applies to it, from rules like `.overlay { animation: name … }`.
    const animations = new Map<string, Set<string>>();
    for (const rule of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
      const name = rule[2].match(/animation:\s*([\w-]+)/)?.[1];
      if (!name) continue;
      for (const cls of rule[1].matchAll(/\.([\w-]+)/g)) {
        const set = animations.get(cls[1]) ?? new Set<string>();
        set.add(name);
        animations.set(cls[1], set);
      }
    }

    // A file that centres with a translate utility AND wears an animated class. Checked per FILE,
    // not per literal: `cn()` splits one element's classes across several strings, and the dialog
    // is exactly that shape.
    const atRisk = new Set<string>();
    for (const [, text] of appSources()) {
      if (!/-translate-[xy]-1\/2/.test(text)) continue;
      for (const [cls, names] of animations) {
        if (new RegExp(`["' \`]${cls}[ "'\`]`).test(text))
          names.forEach((n) => atRisk.add(n));
      }
    }

    // Vacuity guard: this rule must be looking at something. The reduced-motion rule above ran
    // against empty strings once and "passed" — the same mistake is cheap to make twice.
    expect(
      atRisk.size,
      "no animated, self-centring element found — is this rule still wired up?",
    ).toBeGreaterThan(0);

    for (const name of atRisk) {
      const block =
        css.match(
          new RegExp(String.raw`@keyframes\s+${name}\s*\{[\s\S]*?\n\}`),
        )?.[0] ?? "";
      expect(
        block,
        `keyframe "${name}" was not found in the stylesheets`,
      ).not.toBe("");
      expect(
        block,
        `keyframe "${name}" animates transform, which erases the centring translate`,
      ).not.toMatch(new RegExp(String.raw`\btransform\s*:`));
    }
  });

  it("pairs every keyframe with a reduced-motion answer", () => {
    // The structural rule. A keyframe that nobody thought about under reduced motion is exactly the
    // one that will spin forever for the person who asked their OS for calm. If this fails, the fix
    // is to handle the animation in the reduced-motion block — not to delete the assertion.
    for (const [file, css] of Object.entries(styles)) {
      const names = [...css.matchAll(/@keyframes\s+([\w-]+)/g)].map(
        (m) => m[1],
      );
      if (names.length === 0) continue;
      const reducedBlocks = css.match(
        /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*?\n\}|\[data-motion=["']reduced["']\][\s\S]*?\n\}/g,
      );
      expect(
        reducedBlocks,
        `${file} defines keyframes but no reduced-motion block`,
      ).toBeTruthy();
      const reduced = (reducedBlocks ?? []).join("\n");
      // A blanket `animation-duration: 1ms` selector covers every keyframe at once, which is the
      // recommended shape — accept it instead of demanding each name be listed.
      const blanket = /\*[^{]*\{[^}]*animation-duration:\s*1ms/.test(reduced);
      if (blanket) continue;
      for (const name of names) {
        expect(
          reduced,
          `${file}: keyframe "${name}" has no reduced-motion handling`,
        ).toContain(name);
      }
    }
  });
});

describe("accessibility", () => {
  it("keeps a single shared focus ring rather than growing per-component ones", () => {
    // One definition means one place to fix it. The rail and the primitives import `ui/focus`
    // rather than each inventing a ring.
    //
    // This asserted `total >= ratchet.focusVisibleRules`, with the ratchet at 1 — a FLOOR, on a test
    // whose name promises a ceiling. It could not fail for the reason it is named: a ring added to
    // ten more components would only push the number up, and up was the passing direction. It was
    // green at eight while the thing it forbids was happening — `ui/button.tsx`, the component the
    // shared ring was extracted FROM, still carried its own copy, and the two had drifted (the
    // shared one has `relative z-10` so the glow is not clipped; the copy did not).
    //
    // What it counts now is FILES that declare a ring outside `ui/focus.ts`, which is the property
    // the name claims. Two remain and both are legitimate: a scroll container turning the browser
    // default OFF is not a ring.
    const donos = appSources()
      .filter(([file, text]) => !file.endsWith("ui/focus.ts") && /focus-visible:/.test(text))
      .map(([file]) => file);

    expect(
      donos.length,
      `components declaring their own focus ring: ${donos.join(", ")}`,
    ).toBeLessThanOrEqual(ratchet.focusRingOwners);
  });
});

describe("the ratchet", () => {
  it("does not let arbitrary utilities grow", () => {
    // The catch-all: any `utility-[value]` that escaped the specific rules above. It may fall; it
    // may not rise. Whoever needs a new one has to lower a number in ratchet.json to pay for it.
    const { total, where } = countMatches(
      /\b(?:w|h|min-w|min-h|max-w|max-h|p|px|py|pt|pb|pl|pr|m|mx|my|mt|mb|ml|mr|gap|top|left|right|bottom|z|opacity|shadow|leading|tracking)-\[[^\]]+\]/g,
    );
    expect(
      total,
      `arbitrary utilities in: ${where.join(", ")}`,
    ).toBeLessThanOrEqual(ratchet.arbitraryUtilities);
  });
});
