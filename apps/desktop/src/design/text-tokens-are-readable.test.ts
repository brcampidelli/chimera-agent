import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Every colour used for TEXT clears WCAG AA against the theme it belongs to.
 *
 * Found by measuring the running app rather than by reading the CSS: on the light theme, `text-ok`
 * came out at 3.06:1 as 11px text and `text-bad` at 3.82:1, both under the 4.5 that small text
 * needs. The dark theme passed on the same tokens, which is exactly why it survived — the palette
 * is only wrong on one of the two, and only for text.
 *
 * `--warn` already carried the split (`--warn-foreground` is a darker ink for light, a lighter one
 * for dark). `--ok` and `--bad` did not, so `Badge tone="ok"` and every small label reaching for
 * them failed quietly while the badge beside them was correct.
 *
 * This computes the contrast from the token values themselves, in both themes. It needs no browser
 * and no layout, which is the point: the browser measurement that found this cannot run in CI, and
 * a defect nothing can re-check is a defect waiting to come back.
 *
 * **It measures against the badge's tint, not the page.** That distinction is the whole calibration.
 * A `Badge` paints its label on a 15% wash of its own colour (`bg-ok`, `bg-bad`, `bg-warn` at /15) — which pulls the ground toward
 * the ink and costs most of a point of contrast. The first version of this file checked against
 * `--background` and passed an `--ok-foreground` that scored 4.70 there and **4.04** on the tint:
 * a guard that measured the fix in the easy place. `--warn-foreground` had been failing the same
 * way since it was written, at 4.33, with nothing to say so.
 *
 * **What it deliberately does not check.** Fills and icons take the plain token, where the bar is
 * 3:1, and this file has nothing to say about them. It also cannot see which class a component
 * actually reaches for — `panel.tsx`'s tone map is what routes labels to the -foreground pair, and
 * that is guarded by the app's own render tests. Nor does it model a badge sitting on a raised card
 * rather than the page; measured in the running app that costs a further ~0.7, so treat these
 * numbers as the ceiling and keep a margin.
 */

const CSS = readFileSync(join(__dirname, "..", "index.css"), "utf8");

/** `H S% L%` → sRGB, the same arithmetic the browser does for `hsl()`. */
function hslToRgb(spec: string): [number, number, number] {
  const [h, s, l] = spec.trim().split(/\s+/).map((p) => parseFloat(p));
  const S = s / 100;
  const L = l / 100;
  const c = (1 - Math.abs(2 * L - 1)) * S;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = L - c / 2;
  const [r, g, b] =
    h < 60 ? [c, x, 0]
    : h < 120 ? [x, c, 0]
    : h < 180 ? [0, c, x]
    : h < 240 ? [0, x, c]
    : h < 300 ? [x, 0, c]
    : [c, 0, x];
  return [(r + m) * 255, (g + m) * 255, (b + m) * 255];
}

function luminance([r, g, b]: [number, number, number]): number {
  const f = (v: number) => {
    const n = v / 255;
    return n <= 0.03928 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(a: string, b: string): number {
  const [x, y] = [luminance(hslToRgb(a)), luminance(hslToRgb(b))];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** `fill` at 15% over `base` — the ground a `Badge` actually paints its label on.
 *
 *  Writing the class as `bg-<state>` and the alpha in words, because spelling it with a star and a
 *  slash closes the comment: this file failed to parse at all on the first attempt, and the error
 *  pointed four lines past the cause. */
function tinted(fill: string, base: string): [number, number, number] {
  const f = hslToRgb(fill);
  const b = hslToRgb(base);
  return [0, 1, 2].map((i) => f[i] * 0.15 + b[i] * 0.85) as [number, number, number];
}

function contrastOn(ink: string, ground: [number, number, number]): number {
  const [x, y] = [luminance(hslToRgb(ink)), luminance(ground)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** The two `:root` blocks, keyed by which theme they define. */
function themes(): Record<"dark" | "light", Record<string, string>> {
  const blocks = [...CSS.matchAll(/:root[^{]*\{([\s\S]*?)\n\}/g)].map((m) => m[1]);
  expect(blocks.length, "expected two :root blocks — the theme structure changed").toBeGreaterThanOrEqual(2);

  const read = (body: string) => {
    const out: Record<string, string> = {};
    for (const m of body.matchAll(/(--[\w-]+):\s*([^;]+);/g)) out[m[1]] = m[2].trim();
    return out;
  };
  // The dark palette is written first in this file; the light one overrides it below.
  const parsed = blocks.map(read);
  const dark = parsed.find((p) => p["--ok"] && parseFloat(p["--background"]?.split(/\s+/)[2] ?? "50") < 50);
  const light = parsed.find((p) => p["--ok"] && parseFloat(p["--background"]?.split(/\s+/)[2] ?? "50") >= 50);
  expect(dark, "no dark :root block found").toBeTruthy();
  expect(light, "no light :root block found").toBeTruthy();
  return { dark: dark!, light: light! };
}

const AA_SMALL = 4.5;
const INK = ["--ok-foreground", "--bad-foreground", "--warn-foreground", "--muted-foreground", "--foreground"];

describe("text colours", () => {
  const { dark, light } = themes();

  it.each(INK)("%s is readable on the light theme", (token) => {
    const ratio = contrast(light[token], light["--background"]);
    expect(ratio, `${token} is ${ratio.toFixed(2)}:1, needs ${AA_SMALL}`).toBeGreaterThanOrEqual(AA_SMALL);
  });

  it.each(INK)("%s is readable on the dark theme", (token) => {
    const ratio = contrast(dark[token], dark["--background"]);
    expect(ratio, `${token} is ${ratio.toFixed(2)}:1, needs ${AA_SMALL}`).toBeGreaterThanOrEqual(AA_SMALL);
  });

  it("every state colour has an ink counterpart", () => {
    // The shape of the original defect: `--warn` had one and the other two did not, so nothing said
    // which class a label should use and half of them picked the fill.
    for (const state of ["--ok", "--bad", "--warn"]) {
      for (const [name, theme] of [["light", light], ["dark", dark]] as const) {
        expect(theme[`${state}-foreground`], `${state}-foreground missing from the ${name} theme`).toBeTruthy();
      }
    }
  });

  it.each([["--ok", "--ok-foreground"], ["--bad", "--bad-foreground"], ["--warn", "--warn-foreground"]])(
    "%s reads on its own badge tint, in both themes",
    (fill, ink) => {
      for (const [name, theme] of [["light", light], ["dark", dark]] as const) {
        const ratio = contrastOn(theme[ink], tinted(theme[fill], theme["--background"]));
        expect(ratio, `${ink} on ${fill}/15 (${name}) is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(AA_SMALL);
      }
    },
  );

  it("checks a ratio that a wrong value would actually fail", () => {
    // Guarding the guard. `hslToRgb` returning something constant, or `contrast` collapsing to 1,
    // would make every assertion above pass for the wrong reason — so pin one known-bad pairing.
    expect(contrast("152 55% 40%", light["--background"])).toBeLessThan(AA_SMALL);
    expect(contrast(light["--ok-foreground"], light["--background"])).toBeGreaterThanOrEqual(AA_SMALL);
    // And that the tint is doing something: the value this file first shipped passed on the page
    // and failed on the badge, so a `tinted()` that quietly returned the page would hide exactly
    // the defect it was added for.
    expect(contrastOn("152 62% 30%", tinted(light["--ok"], light["--background"]))).toBeLessThan(AA_SMALL);
  });
});
