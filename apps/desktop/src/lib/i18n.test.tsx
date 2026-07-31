import { describe, expect, it } from "vitest";

import { DICTS, LANGS, type Lang } from "@/lib/i18n";

/**
 * The languages are only as real as their key coverage.
 *
 * `t()` falls back to English for a missing key, which is the right runtime behaviour and exactly
 * why this test has to exist: a half-translated locale does not crash, it silently renders English
 * in the middle of Polish, and nobody notices until a user does. Shipping a language in the picker
 * is a claim; these are the assertions that keep the claim true.
 */

const en = DICTS.en;
const others = LANGS.map((l) => l.code).filter((c): c is Lang => c !== "en");

describe("every shipped language", () => {
  it.each(others)("%s covers every English key", (code) => {
    const missing = Object.keys(en).filter((k) => !(k in DICTS[code]));
    expect(missing, `${code} is missing ${missing.length} keys`).toEqual([]);
  });

  it.each(others)("%s has no keys English does not", (code) => {
    // A key that exists only in a translation is dead weight — nothing renders it, and it will
    // drift further from the source with every change.
    const extra = Object.keys(DICTS[code]).filter((k) => !(k in en));
    expect(extra).toEqual([]);
  });

  it.each(others)("%s keeps every {placeholder} intact", (code) => {
    // The one translation bug that produces visible garbage rather than merely wrong words:
    // "{n} tools" translated without its `{n}` renders a sentence with a hole in it, and a
    // `{tokens}` typo renders the literal braces to the user.
    const wrong: string[] = [];
    for (const [key, source] of Object.entries(en)) {
      const want = (source.match(/\{\w+\}/g) ?? []).sort();
      const got = ((DICTS[code][key] ?? "").match(/\{\w+\}/g) ?? []).sort();
      if (want.join(",") !== got.join(",")) wrong.push(`${key}: [${want}] vs [${got}]`);
    }
    expect(wrong).toEqual([]);
  });

  it.each(others)("%s translates something, rather than copying English wholesale", (code) => {
    // A guard against a "translation" that is the English dict renamed. Proper nouns and short
    // technical labels legitimately match (MCP, Git, Chat), so this only asserts that the long
    // strings — the sentences — actually differ.
    const sentences = Object.entries(en).filter(([, v]) => v.length > 60);
    const identical = sentences.filter(([k, v]) => DICTS[code][k] === v);
    expect(identical.length).toBeLessThan(sentences.length * 0.1);
  });
});

describe("the language picker", () => {
  it("offers exactly the languages that have a dictionary", () => {
    expect(LANGS.map((l) => l.code).sort()).toEqual(Object.keys(DICTS).sort());
  });

  it("labels each language in its own language", () => {
    // An endonym, not an exonym: someone looking for their language scans for the word they use
    // for it, not the English name of it.
    const labels = Object.fromEntries(LANGS.map((l) => [l.code, l.label]));
    expect(labels).toMatchObject({
      en: "English",
      pt: "Português",
      es: "Español",
      fr: "Français",
      de: "Deutsch",
      it: "Italiano",
      pl: "Polski",
      zh: "中文",
      ja: "日本語",
    });
  });
});
