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
 * **What it deliberately does not check.** Fills and icons take the plain token, where the bar is
 * 3:1, and this file has nothing to say about them. It also cannot see which class a component
 * actually reaches for — `panel.tsx`'s tone map is what routes labels to the -foreground pair, and
 * that is guarded by the app's own render tests.
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

  it("checks a ratio that a wrong value would actually fail", () => {
    // Guarding the guard. `hslToRgb` returning something constant, or `contrast` collapsing to 1,
    // would make every assertion above pass for the wrong reason — so pin one known-bad pairing.
    expect(contrast("152 55% 40%", light["--background"])).toBeLessThan(AA_SMALL);
    expect(contrast(light["--ok-foreground"], light["--background"])).toBeGreaterThanOrEqual(AA_SMALL);
  });
});
