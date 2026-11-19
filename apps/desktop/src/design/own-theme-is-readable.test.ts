import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ACCENTS, CONTRASTS, TINTS, type Accent, type Contrast, type Tint } from "@/lib/theme";

/**
 * Your own theme stays readable in every combination it can be set to.
 *
 * Settings › Appearance lets someone pick an accent, a surface tint and a contrast level on top of
 * light or dark: 2 × 6 × 3 × 2 = 72 palettes, of which the shipped two are the only ones anyone
 * would look at by default. `text-tokens-are-readable.test.ts` measures those two. This file builds
 * each of the 72 from the same `index.css` the browser reads — base theme, then the tint block, then
 * contrast, then accent, the order the cascade applies them — and holds every text colour to the
 * same bar: 4.5:1 on the page, and on its own badge wash.
 *
 * Same arithmetic as the sibling file, repeated rather than imported: a test that shares its ruler
 * with the thing it guards stops being a second opinion.
 */

const CSS = readFileSync(join(__dirname, "..", "index.css"), "utf8");

function hslToRgb(spec: string): [number, number, number] {
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

function luminance([r, g, b]: [number, number, number]): number {
  const f = (v: number) => {
    const n = v / 255;
    return n <= 0.03928 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function ratio(a: [number, number, number], b: [number, number, number]): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** `fill` at 15% over `base`: the ground a badge paints its label on. */
function tinted(fill: string, base: string): [number, number, number] {
  const f = hslToRgb(fill);
  const b = hslToRgb(base);
  return [0, 1, 2].map((i) => f[i] * 0.15 + b[i] * 0.85) as [number, number, number];
}

type Tokens = Record<string, string>;

/** Every `:root…{}` block, with the selector it is keyed on. */
const BLOCKS = [...CSS.matchAll(/(:root[^{]*)\{([\s\S]*?)\n[ \t]*\}/g)].map((m) => {
  const tokens: Tokens = {};
  for (const t of m[2].matchAll(/(--[\w-]+):\s*([^;]+);/g)) tokens[t[1]] = t[2].trim();
  return { selector: m[1].trim(), tokens };
});

type Theme = "dark" | "light";

/** The block for one option in one theme, by the selector shape `index.css` documents. */
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

function palette(theme: Theme, tint: Tint, contrast: Contrast, accent: Accent): Tokens {
  return {
    ...base(theme),
    ...(tint === "navy" ? {} : optionBlock(theme, "tint", tint)),
    ...(contrast === "standard" ? {} : optionBlock(theme, "contrast", contrast)),
    ...(contrast === "high" && tint !== "navy"
      ? BLOCKS.find((b) =>
          b.selector.startsWith(theme === "light" ? ":root[data-theme='light']" : ":root:not([data-theme='light'])") &&
          b.selector.includes(`[data-tint='${tint}']`) && b.selector.includes("[data-contrast='high']"),
        )?.tokens
      : {}),
    ...(accent === "blue" ? {} : optionBlock(theme, "accent", accent)),
  };
}

const THEMES: Theme[] = ["dark", "light"];
const INK = ["--foreground", "--muted-foreground", "--accent-ink", "--ok-foreground", "--bad-foreground", "--warn-foreground"];
const BADGES: [string, string][] = [
  ["--accent", "--accent-ink"],
  ["--ok", "--ok-foreground"],
  ["--bad", "--bad-foreground"],
  ["--warn", "--warn-foreground"],
];

const COMBOS = THEMES.flatMap((theme) =>
  TINTS.flatMap((tint) =>
    CONTRASTS.flatMap((contrast) => ACCENTS.map((accent) => ({ theme, tint, contrast, accent }))),
  ),
);

describe("your own theme", () => {
  it("defines every non-default option in both themes", () => {
    // A value the select offers with no block behind it would quietly show the default palette.
    for (const theme of THEMES) {
      for (const accent of ACCENTS.filter((a) => a !== "blue")) {
        expect(optionBlock(theme, "accent", accent), `accent ${accent} (${theme})`).toBeTruthy();
      }
      for (const tint of TINTS.filter((t) => t !== "navy")) {
        expect(optionBlock(theme, "tint", tint), `tint ${tint} (${theme})`).toBeTruthy();
      }
      expect(optionBlock(theme, "contrast", "high"), `contrast high (${theme})`).toBeTruthy();
    }
  });

  it("covers all 72 combinations", () => {
    expect(COMBOS).toHaveLength(72);
  });

  it.each(COMBOS)("$theme · $tint · $contrast · $accent: text reads at 4.5:1", ({ theme, tint, contrast, accent }) => {
    const p = palette(theme, tint, contrast, accent);
    const page = hslToRgb(p["--background"]);
    const card = hslToRgb(p["--card"]);
    for (const token of INK) {
      const onPage = ratio(hslToRgb(p[token]), page);
      expect(onPage, `${token} on the page is ${onPage.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
    }
    // The foreground and the secondary text on a card, where most of the app's text actually sits.
    for (const token of ["--foreground", "--muted-foreground"]) {
      const onCard = ratio(hslToRgb(p[token]), card);
      expect(onCard, `${token} on a card is ${onCard.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
    }
    for (const [fill, ink] of BADGES) {
      const onTint = ratio(hslToRgb(p[ink]), tinted(p[fill], p["--background"]));
      expect(onTint, `${ink} on ${fill}/15 is ${onTint.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
    }
    // Text ON a filled accent button. The bar is 3:1 (the shipped dark blue sits at 3.4 with white);
    // a hue light enough to fail it with white takes navy ink instead.
    const onFill = ratio(hslToRgb(p["--accent-foreground"]), hslToRgb(p["--accent"]));
    expect(onFill, `--accent-foreground on --accent is ${onFill.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
  });

  it("would catch an unreadable value", () => {
    // Guarding the guard: a palette builder that silently fell back to the shipped theme would pass
    // every case above for the wrong reason.
    const amberLight = palette("light", "navy", "standard", "amber");
    expect(amberLight["--accent-ink"]).toBe("38 92% 26%");
    expect(ratio(hslToRgb("38 92% 46%"), hslToRgb(amberLight["--background"]))).toBeLessThan(4.5);
    expect(palette("dark", "warm", "high", "rose")["--background"]).toBe("30 14% 8%");
  });
});
