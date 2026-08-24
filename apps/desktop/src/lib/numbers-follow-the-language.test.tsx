import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { I18nProvider, useNum } from "@/lib/i18n";

/**
 * A number is grouped the way the CHOSEN language groups it, not the way the machine does.
 *
 * `Number.toLocaleString()` with no argument follows the operating system. On a pt-BR machine with
 * the app set to English, 4200 renders "4.200" and the English sentence around it — "4.200 tokens
 * saved across 3 delegated runs" — reads as four point two. A thousandfold misread, in the one
 * place where the number IS the content.
 *
 * Found by a test failing for the right reason and the wrong cause: an assertion expecting "4,200"
 * met "4.200" and the first instinct was to loosen the assertion. The separator was the defect.
 *
 * Two halves, because either alone would pass while the app stayed wrong: the behaviour has to
 * change with the language, and no call site may go back to the locale-free form.
 */

function Amostra({ n }: { n: number }) {
  const num = useNum();
  return <span data-testid="n">{num(n)}</span>;
}

function renderIn(lang: string, n: number): string {
  localStorage.setItem("chimera.lang", lang);
  const { unmount } = render(
    <I18nProvider>
      <Amostra n={n} />
    </I18nProvider>,
  );
  const out = screen.getByTestId("n").textContent ?? "";
  unmount();
  return out;
}

const SRC = join(__dirname, "..");

function sources(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) sources(full, out);
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

describe("numbers", () => {
  it("groups them differently in English and Portuguese", () => {
    const en = renderIn("en", 1234567);
    const pt = renderIn("pt", 1234567);

    expect(en, "English should group with commas").toContain(",");
    expect(pt, "Portuguese should group with dots").toContain(".");
    expect(en).not.toBe(pt);
  });

  it("does not follow the machine", () => {
    // The property that was broken. `toLocaleString()` with no argument gives ONE answer per
    // machine; this must give a different one per language on the same machine, which the
    // assertion above already proves — so this pins the direction rather than repeating it.
    expect(renderIn("de", 1234567)).toBe("1.234.567");
    expect(renderIn("en", 1234567)).toBe("1,234,567");
  });

  it("leaves no call site on the locale-free form", () => {
    // `useNum` existing changes nothing if the five call sites still call the bare method. Scanned
    // over production source only: a test may legitimately write `toLocaleString()` to describe
    // what the runtime does, and `i18n.tsx` is where the one legitimate `Intl` construction lives.
    const offenders: string[] = [];
    for (const file of sources(SRC)) {
      if (file.endsWith(join("lib", "i18n.tsx"))) continue;
      const text = readFileSync(file, "utf8");
      // The empty-argument form specifically. `toLocaleString("pt")` is a deliberate choice and
      // this has nothing to say about it.
      if (/toLocaleString\(\s*\)/.test(text)) offenders.push(file.slice(SRC.length + 1));
    }
    expect(offenders, "call sites that follow the OS instead of the app").toEqual([]);
  });

  it("would notice a call site coming back", () => {
    // Guarding the guard: the regex above is the whole check, and a regex that matches nothing
    // passes for the wrong reason.
    expect(/toLocaleString\(\s*\)/.test("const x = n.toLocaleString();")).toBe(true);
    expect(/toLocaleString\(\s*\)/.test('const x = n.toLocaleString("pt");')).toBe(false);
  });
});
