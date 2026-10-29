import { describe, expect, it } from "vitest";

import { artboardSrcDoc, tokensCss } from "@/lib/design/htmlArtboard";
import { channelsToHex } from "@/lib/design/defaults";
import type { DesignTokens } from "@/lib/design/model";

const tokens: DesignTokens = {
  colors: { zebra: channelsToHex(17, 34, 51), alpha: channelsToHex(170, 187, 204) },
  typography: {
    zebra: { family: 'A "quoted" font', size: 12, weight: 500, lineHeight: 1.25 },
    alpha: { family: "Inter", size: 16, weight: 400 },
  },
  spacing: { xl: 20, sm: 4 },
  radii: { round: 8, tiny: 2 },
};

describe("HTML artboard iframe document", () => {
  it("injects the CSP before HTML starting with a comment", () => {
    const source = artboardSrcDoc("<!-- opening --><main>content</main>", tokens);
    expect(source.indexOf('http-equiv="Content-Security-Policy"')).toBeLessThan(source.indexOf("<!-- opening -->"));
  });

  it("keeps a BOM first and injects policy before content", () => {
    const source = artboardSrcDoc("\uFEFF<div>content</div>", tokens);
    expect(source.startsWith("\uFEFF<meta http-equiv=\"Content-Security-Policy\"" )).toBe(true);
    expect(source.indexOf('http-equiv="Content-Security-Policy"')).toBeLessThan(source.indexOf("<div>"));
  });

  it("leaves an uppercase leading doctype first and places policy immediately after it", () => {
    const source = artboardSrcDoc("<!DOCTYPE html><html><body>content</body></html>", tokens);
    expect(source.startsWith("<!DOCTYPE html><meta http-equiv=\"Content-Security-Policy\"")).toBe(true);
    expect(source.indexOf('http-equiv="Content-Security-Policy"')).toBeLessThan(source.indexOf("<html>"));
  });

  it("puts our policy before any model-authored CSP meta", () => {
    const source = artboardSrcDoc('<meta http-equiv="Content-Security-Policy" content="default-src *"><p>x</p>', tokens);
    expect(source.indexOf("script-src 'none'")).toBeLessThan(source.indexOf('content="default-src *"'));
  });

  it("writes token variables in exact group and sorted-name order", () => {
    expect(tokensCss(tokens)).toBe(':root{--ds-color-alpha: #aabbcc;--ds-color-zebra: #112233;--ds-font-alpha-family: "Inter";--ds-font-alpha-size: 16px;--ds-font-alpha-weight: 400;--ds-font-zebra-family: "A \\\"quoted\\\" font";--ds-font-zebra-size: 12px;--ds-font-zebra-weight: 500;--ds-font-zebra-line-height: 1.25;--ds-space-sm: 4px;--ds-space-xl: 20px;--ds-radius-round: 8px;--ds-radius-tiny: 2px}');
  });
});

describe("the policy comes before the model's own markup", () => {
  it.each<[string, string, string]>([
    ["leading whitespace", "  \n\t<!doctype html><main>x</main>", "<main>"],
    ["a comment closed by --!> before the doctype", "<!-- a --!> <!doctype html><main>x</main>", "<main>"],
    ["an unclosed comment", "<!-- never closed <main>x</main>", "<!-- never closed"],
    ["a BOM, a comment and a doctype", "﻿<!-- c --><!doctype html><main>x</main>", "<main>"],
    ["a body that starts with <html lang=\"pt\">", '<html lang="pt"><main>x</main></html>', '<html lang="pt">'],
    ["a body that starts with <head>", "<head><title>x</title></head><main>x</main>", "<head>"],
  ])("with %s", (_label, html, firstModelTag) => {
    const source = artboardSrcDoc(html, tokens);
    const policy = source.indexOf('http-equiv="Content-Security-Policy"');
    expect(policy).toBeGreaterThanOrEqual(0);
    expect(policy).toBeLessThan(source.indexOf(firstModelTag));
    expect(source).toContain(html.slice(html.indexOf(firstModelTag)));
  });
});

describe("tokensCss drops any value that could leave its declaration", () => {
  // Each case puts one hostile value in one slot of an otherwise valid document. The `ok` entries
  // must survive, and the bad one must be absent without a trace of its markup.
  const BAD = "</style><img src=x>";
  const base = (): DesignTokens => ({
    colors: { ok: "#aabbcc", bad: "#112233" },
    typography: {
      ok: { family: "Inter", size: 16, weight: 400 },
      bad: { family: "Inter", size: 12, weight: 500, lineHeight: 1.25 },
    },
    spacing: { ok: 4, bad: 8 },
    radii: { ok: 2, bad: 6 },
  });

  it.each<[string, () => DesignTokens, string]>([
    ["a colour name", () => ({ ...base(), colors: { ok: "#aabbcc", [BAD]: "#112233" } }), "#112233"],
    ["a colour value", () => ({ ...base(), colors: { ok: "#aabbcc", bad: BAD } }), "</style>"],
    ["a colour value that is almost hex", () => ({ ...base(), colors: { ok: "#aabbcc", bad: "#112233;x" } }), "#112233"],
    ["a font name", () => ({ ...base(), typography: { ok: { family: "Inter", size: 16, weight: 400 }, [BAD]: { family: "Inter", size: 12, weight: 500 } } }), "12px"],
    ["a font family", () => ({ ...base(), typography: { ...base().typography, bad: { family: BAD, size: 12, weight: 500 } } }), "--ds-font-bad-family"],
    ["a font family with braces", () => ({ ...base(), typography: { ...base().typography, bad: { family: "Inter}body{x", size: 12, weight: 500 } } }), "--ds-font-bad-family"],
    ["a font size", () => ({ ...base(), typography: { ...base().typography, bad: { family: "Inter", size: BAD as unknown as number, weight: 500 } } }), "--ds-font-bad-size"],
    ["a font weight", () => ({ ...base(), typography: { ...base().typography, bad: { family: "Inter", size: 12, weight: BAD as unknown as number } } }), "--ds-font-bad-weight"],
    ["a line height", () => ({ ...base(), typography: { ...base().typography, bad: { family: "Inter", size: 12, weight: 500, lineHeight: BAD as unknown as number } } }), "--ds-font-bad-line-height"],
    ["a non-finite font size", () => ({ ...base(), typography: { ...base().typography, bad: { family: "Inter", size: Number.NaN, weight: 500 } } }), "--ds-font-bad-size"],
    ["a spacing name", () => ({ ...base(), spacing: { ok: 4, [BAD]: 8 } }), ": 8px"],
    ["a spacing value", () => ({ ...base(), spacing: { ok: 4, bad: BAD as unknown as number } }), "--ds-space-bad"],
    ["an infinite spacing value", () => ({ ...base(), spacing: { ok: 4, bad: Number.POSITIVE_INFINITY } }), "--ds-space-bad"],
    // ": 6px" rather than "6px": the bare string is inside "16px", which the valid entries contain.
    ["a radius name", () => ({ ...base(), radii: { ok: 2, [BAD]: 6 } }), ": 6px"],
    ["a radius value", () => ({ ...base(), radii: { ok: 2, bad: BAD as unknown as number } }), "--ds-radius-bad"],
  ])("drops %s and keeps the valid entries", (_label, build, absent) => {
    const css = tokensCss(build());
    expect(css).not.toContain("<");
    expect(css).not.toContain("img");
    expect(css).not.toContain(absent);
    expect(css).toContain("--ds-color-ok: #aabbcc");
    expect(css).toContain("--ds-font-ok-size: 16px");
    expect(css).toContain("--ds-space-ok: 4px");
    expect(css).toContain("--ds-radius-ok: 2px");
  });
});
