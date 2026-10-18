import { useEffect, useState, type ReactNode } from "react";
import { ChevronDown, Minus, Pin, PinOff } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { cn } from "@/lib/utils";

/** Set once this webview has read the layout's `composer.config` mode under the rule below. Before
 *  the rule, ONE click on the chip line stored "open" in the layout for good, so a person who opened
 *  the pickers once to change a model met them open — half the composer's height — on every visit
 *  after. A layout from that time cannot tell "I clicked once" from "keep it open", so the first read
 *  under the new rule puts it back to the default, closed, and only an explicit "Keep open" sets it
 *  open again. */
export const COMPOSER_SETTINGS_RULE_KEY = "chimera:composer:settings:rule";

function ruleRead(): boolean {
  try {
    return localStorage.getItem(COMPOSER_SETTINGS_RULE_KEY) === "1";
  } catch {
    // No storage: nothing was stored before either, so there is nothing to reset.
    return true;
  }
}

function markRuleRead(): void {
  try {
    localStorage.setItem(COMPOSER_SETTINGS_RULE_KEY, "1");
  } catch {
    // Read again next launch; resetting an open panel twice costs one click.
  }
}

/**
 * Who runs the turn, at what cost profile, on which model: one line of chips by default (dynamic
 * screen, phase 4). The pickers take four rows above the composer, and they are set far less often
 * than they are looked past.
 *
 * Two ways to be open, on purpose. A click on the chips opens the pickers for THIS visit: it closes
 * again when a turn starts (the pickers are disabled while one runs anyway), when the screen is left,
 * or with Minimise — so changing the model once no longer leaves the panel open for good. "Keep open"
 * is the remembered choice: it lives in the layout, per person, and only it survives a reload.
 *
 * It never closes, and the posture note beside it is not part of it: the warning that commands would
 * run on this machine with no OS sandbox stays on screen whatever this is set to, which is why the
 * layout refuses to close `composer.config` at all.
 */
export function ComposerSettings({
  summary,
  collapseWhen = false,
  children,
}: {
  summary: string[];
  /** True while a turn runs: a panel opened for this visit folds back to its line. */
  collapseWhen?: boolean;
  children: ReactNode;
}) {
  const t = useT();
  const { layout, dispatch } = useLayout();
  const kept = layout.panels["composer.config"].mode !== "minimized";
  const [visiting, setVisiting] = useState(false);
  const name = t("layout.panel.composer.config");

  useEffect(() => {
    if (ruleRead()) return;
    markRuleRead();
    if (kept) dispatch({ type: "set-mode", panel: "composer.config", mode: "minimized" });
    // Once, on the first mount under the rule: a later "Keep open" must not be undone by this.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (collapseWhen) setVisiting(false);
  }, [collapseWhen]);

  const open = kept || visiting;
  const expandLabel = t("layout.card.expand", { name });
  const minimizeLabel = t("layout.card.minimize", { name });
  const keepLabel = t(kept ? "composer.settings.unkeep" : "composer.settings.keep");
  const iconButton = cn(
    "shrink-0 rounded-sm p-0.5 text-muted-foreground transition-colors duration-1 ease-out",
    "hover:bg-surface-hover hover:text-foreground",
    focusRing,
  );

  if (!open) {
    // The chips and the chevron are one button. This line is how every change of model or profile
    // begins — and a 14-pixel chevron at the far end was the only part of it that answered a click
    // (Fitts: the target is the thing you are already looking at).
    return (
      <div className="flex min-w-0 items-center" data-testid="composer-settings-line">
        <Tooltip label={expandLabel}>
          <button
            type="button"
            aria-label={expandLabel}
            aria-expanded={false}
            onClick={() => setVisiting(true)}
            className={cn(
              "flex min-w-0 flex-wrap items-center gap-1 rounded-chip p-0.5 text-left",
              "transition-colors duration-1 ease-out hover:bg-surface-hover",
              focusRing,
            )}
          >
            {summary.map((chip) => (
              <span key={chip} className="rounded-chip border border-hairline px-2 py-0.5 text-xs text-foreground">
                {chip}
              </span>
            ))}
            <ChevronDown aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          </button>
        </Tooltip>
      </div>
    );
  }
  return (
    <div className="flex min-w-0 items-start gap-1.5">
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">{children}</div>
      <div className="flex shrink-0 flex-col gap-0.5">
        <Tooltip label={minimizeLabel}>
          <button
            type="button"
            aria-label={minimizeLabel}
            aria-expanded
            onClick={() => {
              setVisiting(false);
              if (kept) dispatch({ type: "set-mode", panel: "composer.config", mode: "minimized" });
            }}
            className={iconButton}
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
        </Tooltip>
        <Tooltip label={keepLabel}>
          <button
            type="button"
            aria-label={keepLabel}
            aria-pressed={kept}
            onClick={() => {
              markRuleRead();
              // Un-keeping leaves it open for the rest of this visit: the person is looking at it.
              setVisiting(kept);
              dispatch({ type: "set-mode", panel: "composer.config", mode: kept ? "minimized" : "open" });
            }}
            className={iconButton}
          >
            {kept ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
          </button>
        </Tooltip>
      </div>
    </div>
  );
}
