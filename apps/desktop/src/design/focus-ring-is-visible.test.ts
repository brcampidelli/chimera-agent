import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ACCENTS, CONTRASTS, TINTS, type Accent, type Contrast, type Tint } from "@/lib/theme";

/**
 * The keyboard focus ring is visible in every palette the app can be set to.
 *
 * WCAG 1.4.11 asks 3:1 of the indicator against what it sits on. The ring used to be
 * `shadow-glow` — a 3 px halo of `--ring` at 20–28% alpha — behind `outline-hidden`, which turned the
 * browser's own outline (usually well above 3:1) OFF. Measured on the page it read 1.12–1.35:1 in the
 * light theme and 1.38–1.96:1 in the dark one, in all 72 palettes, and nothing failed because no test
 * looked at the ring: the text gates measure ink, and the ring is not ink.
 *
 * What is measured here is what `ui/focus.ts` actually paints, not what this file assumes: a solid
 * `outline-ring` is `--ring` at full opacity; anything else is read as the translucent halo it
 * replaced, blended over the ground. So the same test fails on the old definition and passes on the
 * new one, instead of passing for the wrong reason.
 *
 * Same arithmetic as `own-theme-is-readable.test.ts`, repeated rather than imported: a test that
 * shares its ruler with the thing it guards stops being a second opinion.
 */

const CSS = readFileSync(join(__dirname, "..", "index.css"), "utf8").replace(/\r\n/g, "\n");
const FOCUS = readFileSync(join(__dirname, "..", "components", "ui", "focus.ts"), "utf8");

type Rgb = [number, number, number];

function hslToRgb(spec: string): Rgb {
  const [h, s, l] = spec.split("/")[0].trim().split(/\s+/).map((p) => parseFloat(p));
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

function luminance([r, g, b]: Rgb): number {
  const f = (v: number) => {
    const n = v / 255;
    return n <= 0.03928 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function ratio(a: Rgb, b: Rgb): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

type Tokens = Record<string, string>;
type Theme = "dark" | "light";

const BLOCKS = [...CSS.matchAll(/(:root[^{]*)\{([\s\S]*?)\n[ \t]*\}/g)].map((m) => {
  const tokens: Tokens = {};
  for (const t of m[2].matchAll(/(--[\w-]+):\s*([^;]+);/g)) tokens[t[1]] = t[2].trim();
  return { selector: m[1].trim(), tokens };
});

function optionBlock(theme: Theme, attr: string, value: string): Tokens | undefined {
  const wanted = `[data-${attr}='${value}']`;
  return BLOCKS.find(
    (b) =>
      b.selector.includes(wanted) &&
      (theme === "light"
        ? b.selector.startsWith(":root[data-theme='light']")
        : b.selector.startsWith(":root:not([data-theme='light'])")),
  )?.tokens;
}

function base(theme: Theme): Tokens {
  const found = BLOCKS.find((b) =>
    theme === "light" ? b.selector === ":root[data-theme='light']" : b.selector === ":root" && b.tokens["--ok"],
  );
  expect(found, `no base block for ${theme}`).toBeTruthy();
  return theme === "light" ? { ...base("dark"), ...found!.tokens } : found!.tokens;
}

/** Cascade order as `own-theme-is-readable.test.ts` builds it: base, tint, contrast, accent. */
function palette(theme: Theme, tint: Tint, contrast: Contrast, accent: Accent): Tokens {
  return {
    ...base(theme),
    ...(tint === "navy" ? {} : optionBlock(theme, "tint", tint)),
    ...(contrast === "standard" ? {} : optionBlock(theme, "contrast", contrast)),
    ...(accent === "blue" ? {} : optionBlock(theme, "accent", accent)),
  };
}

/** The surfaces a focused control sits on: the page, a card, a `muted` well, a raised row. */
const GROUNDS = ["--background", "--card", "--muted", "--surface-2"];

const SOLID = /focus-visible:outline-ring\b/.test(FOCUS) && !/focus-visible:outline-hidden/.test(FOCUS);

/** The ring as painted over one ground: opaque `--ring` for an outline, the old halo otherwise. */
function paintedRing(p: Tokens, ground: Rgb): Rgb {
  const ring = hslToRgb(p["--ring"]);
  if (SOLID) return ring;
  const alpha = parseFloat(/hsl\(var\(--ring\)\s*\/\s*([\d.]+)\)/.exec(p["--glow"] ?? "")?.[1] ?? "1");
  return [0, 1, 2].map((i) => ring[i] * alpha + ground[i] * (1 - alpha)) as Rgb;
}

const THEMES: Theme[] = ["dark", "light"];
const COMBOS = THEMES.flatMap((theme) =>
  TINTS.flatMap((tint) =>
    CONTRASTS.flatMap((contrast) => ACCENTS.map((accent) => ({ theme, tint, contrast, accent }))),
  ),
);

function worst(p: Tokens): { token: string; value: number } {
  let low = { token: "", value: Infinity };
  for (const token of GROUNDS) {
    const ground = hslToRgb(p[token]);
    const r = ratio(paintedRing(p, ground), ground);
    if (r < low.value) low = { token, value: r };
  }
  return low;
}

describe("the keyboard focus ring", () => {
  it("is a solid outline, not a halo behind a hidden outline", () => {
    expect(FOCUS, "focusRing must draw an outline in --ring").toMatch(/focus-visible:outline-ring\b/);
    expect(FOCUS, "outline-hidden switches the browser's own ring off").not.toMatch(
      /focus-visible:outline-hidden/,
    );
  });

  it("covers all 72 combinations", () => {
    expect(COMBOS).toHaveLength(72);
  });

  it.each(COMBOS)(
    "$theme · $tint · $contrast · $accent: ring reaches 3:1 on every ground it sits on",
    ({ theme, tint, contrast, accent }) => {
      const { token, value } = worst(palette(theme, tint, contrast, accent));
      expect(value, `ring on ${token} is ${value.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
    },
  );

  it.each(COMBOS.filter((c) => c.contrast === "high"))(
    "$theme · $tint · high · $accent: high contrast takes the ring to 4.5:1 and thickens it",
    ({ theme, tint, contrast, accent }) => {
      const p = palette(theme, tint, contrast, accent);
      const { token, value } = worst(p);
      expect(value, `ring on ${token} is ${value.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
      expect(p["--ring-width"], "high contrast sets a thicker ring").toBe("3px");
    },
  );

  it("keeps the standard ring at 2px", () => {
    expect(base("dark")["--ring-width"]).toBe("2px");
    expect(base("light")["--ring-width"]).toBe("2px");
  });

  it("survives Windows forced colors, where box-shadow is dropped", () => {
    const forced = /@media\s*\(forced-colors:\s*active\)\s*\{([\s\S]*?)\n\s{0,2}\}\n/.exec(CSS)?.[1] ?? "";
    expect(forced, "index.css has no forced-colors block for the ring").not.toBe("");
    expect(forced).toMatch(/:focus-visible/);
    expect(forced).toMatch(/outline:[^;]*(Highlight|CanvasText)/);
  });

  it("would catch a halo (guarding the guard)", () => {
    // The shipped dark blue at 28% over the page — the old ring — is the number this file exists to
    // refuse. If this stopped reading below 3, the blend above would be measuring nothing.
    const p = palette("dark", "navy", "standard", "blue");
    const ground = hslToRgb(p["--background"]);
    const ring = hslToRgb(p["--ring"]);
    const halo = [0, 1, 2].map((i) => ring[i] * 0.28 + ground[i] * 0.72) as Rgb;
    expect(ratio(halo, ground)).toBeLessThan(3);
  });
});
