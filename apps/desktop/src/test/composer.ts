import { COMPOSER_SETTINGS_RULE_KEY } from "@/components/code/ComposerSettings";
import { defaultLayout } from "@/lib/layout/model";
import { STORAGE_KEY } from "@/lib/layout/store";

/**
 * Store a Code layout whose composer pickers are open.
 *
 * They start as one line of chips. A test about what a picker sends needs the picker on screen, the
 * way someone who wants to change it clicks the line open first; the default itself is asserted in
 * the layout model's own tests. Stored open means "Keep open" since study 34 (C6): the rule flag is set
 * with it, as a keep made under the rule sets it, so the one-time reset of older layouts leaves it be.
 */
export function withComposerPickersOpen(): void {
  const layout = defaultLayout();
  layout.panels["composer.config"] = { ...layout.panels["composer.config"], mode: "open" };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  localStorage.setItem(COMPOSER_SETTINGS_RULE_KEY, "1");
}
