import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ShortcutsDialog } from "@/components/shell/ShortcutsDialog";
import { formatKeys, SHORTCUTS } from "@/lib/hotkeys";
import { DICTS, I18nProvider, LANGS } from "@/lib/i18n";

function renderDialog() {
  return render(
    <I18nProvider>
      <ShortcutsDialog open onOpenChange={() => undefined} />
    </I18nProvider>,
  );
}

describe("ShortcutsDialog", () => {
  it("names every shortcut in all ten languages", () => {
    expect(new Set(SHORTCUTS.map(({ id }) => id)).size).toBe(SHORTCUTS.length);
    expect(LANGS).toHaveLength(10);
    for (const language of LANGS) {
      const missing = SHORTCUTS.filter(({ labelKey }) => !(labelKey in DICTS[language.code])).map((s) => s.labelKey);
      expect(missing, language.code).toEqual([]);
    }
  });

  it("lists exactly the shared table: every row, in its order, with its keys, and nothing else", () => {
    renderDialog();
    const sheet = screen.getByRole("dialog", { name: DICTS.en["hotkeys.title"] });
    const terms = within(sheet).getAllByRole("term");
    const definitions = within(sheet).getAllByRole("definition");
    expect(terms.map((el) => el.textContent)).toEqual(SHORTCUTS.map((s) => DICTS.en[s.labelKey]));
    expect(definitions.map((el) => el.textContent)).toEqual(SHORTCUTS.map((s) => formatKeys(s.keys)));
  });
});
