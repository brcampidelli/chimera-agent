import { describe, expect, it } from "vitest";

import pending from "@/lib/i18n-pending.json";
import { DICTS, LANGS, type Lang } from "@/lib/i18n";

/**
 * The languages are only as real as their key coverage.
 *
 * `t()` falls back to English for a missing key, which is the right runtime behaviour and exactly
 * why this test has to exist: a half-translated locale does not crash, it silently renders English
 * in the middle of Polish, and nobody notices until a user does. Shipping a language in the picker
 * is a claim; these are the assertions that keep the claim true.
 *
 * **Why there is an escape hatch.** Demanding total parity made the claim true and the app closed:
 * adding one English string turned eight locales red, so any contributor who was not fluent in nine
 * languages simply could not touch the desktop app. That is a real cost and it bought less than it
 * looked like, because the runtime fallback means a missing key was never a crash — only a silently
 * English sentence. So the guarantee moved from *"everything is translated"* to *"nothing
 * untranslated is invisible"*: a key may be listed in `i18n-pending.json` and CI stays green, and
 * the tests below then push that list back down to nothing. Declared debt, with the debt visible.
 */

const en = DICTS.en;
const others = LANGS.map((l) => l.code).filter((c): c is Lang => c !== "en");
const PENDING: string[] = pending.keys;

describe("every shipped language", () => {
  it.each(others)("%s covers every English key that is not declared pending", (code) => {
    const missing = Object.keys(en).filter((k) => !(k in DICTS[code]) && !PENDING.includes(k));
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
      // Only judge what the locale actually has. An absent key is the coverage test's business
      // (and may be legitimately pending); scoring it here would report a placeholder bug for a
      // string that simply has not been translated yet.
      if (!(key in DICTS[code])) continue;
      const want = (source.match(/\{\w+\}/g) ?? []).sort();
      const got = (DICTS[code][key].match(/\{\w+\}/g) ?? []).sort();
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

describe("the pending-translation list", () => {
  // An escape hatch nobody cleans up is just a slower version of the thing it replaced. These are
  // the assertions that make the list a queue rather than a drawer.

  it("only names keys that exist in English", () => {
    // A key deleted from `en` but left here would keep its exemption forever, and would quietly
    // permit a locale to be missing something that no longer exists — an exemption for nothing.
    const dead = PENDING.filter((k) => !(k in en));
    expect(dead, `remove these from i18n-pending.json: ${dead.join(", ")}`).toEqual([]);
  });

  it("drops a key once every language has it", () => {
    // The self-cleaning half. Without it the list only ever grows: a translator does the work, and
    // the entry sits there granting an exemption that is no longer used by anyone.
    const done = PENDING.filter((k) => others.every((code) => k in DICTS[code]));
    expect(done, `these are fully translated — delete them from i18n-pending.json: ${done.join(", ")}`)
      .toEqual([]);
  });

  it("stays inside its budget", () => {
    expect(PENDING.length).toBeLessThanOrEqual(pending.maxPending);
  });

  it("has no duplicate entries", () => {
    expect(PENDING.length).toBe(new Set(PENDING).size);
  });
});
