import * as Menu from "@radix-ui/react-dropdown-menu";
import { PanelsTopLeft } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useT, type TFunc } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import type { HiddenItem } from "@/lib/layout/model";
import { cn } from "@/lib/utils";

/**
 * Everything the person hid, one click from the status bar, and the way back to the default layout.
 *
 * The plan for the dynamic screen (approved 2026-09-29) makes this the first of seven ways back, and
 * the one that needs no shortcut remembered: whatever is closed or hidden is listed here with "Show".
 *
 * **Nothing at zero.** The plan drew this as a count that is always on screen. It is not, and that is
 * this repository's own rule overriding the plan: `PendingApprovals` renders nothing while no question
 * waits, because "an indicator that is always on screen is one people learn to stop seeing". A tray
 * that says 0 is that indicator. "Restore the default layout" stays reachable with nothing hidden,
 * from the command palette, which is where a person looks for a command rather than for a state.
 */
export function HiddenTray() {
  const t = useT();
  const { hidden, dispatch } = useLayout();
  const n = hidden.length;
  if (n === 0) return null;

  const label = t("layout.hidden.label", { n });
  const item = cn(
    "flex cursor-default items-center gap-2 rounded-md px-2 py-1 text-xs outline-hidden",
    "data-highlighted:bg-surface-hover",
  );
  return (
    <Menu.Root>
      <Menu.Trigger
        // The visible text is the count alone, like the approvals chip beside it; the name carries
        // the sentence and contains the visible digits, so a voice user can say what they see.
        aria-label={label}
        title={label}
        className={cn(
          "flex items-center gap-1.5 rounded-chip border border-hairline px-2 py-0.5",
          "transition-colors duration-1 ease-out hover:text-foreground",
          focusRing,
        )}
      >
        <PanelsTopLeft className="h-3 w-3" aria-hidden />
        {n}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          side="top"
          align="end"
          sideOffset={6}
          className="overlay floating z-50 min-w-56 p-1 text-foreground"
        >
          <Menu.Label className="px-2 py-1 text-xs font-medium text-muted-foreground">
            {t("layout.hidden.title")}
          </Menu.Label>
          {hidden.map((h) => (
            <Menu.Item key={`${h.kind}:${h.id}`} className={item} onSelect={() => dispatch(h.restore)}>
              <span className="min-w-0 flex-1 truncate">{nameOf(h, t)}</span>
              <span className="text-accent-ink">{t("layout.hidden.show")}</span>
            </Menu.Item>
          ))}
          <Menu.Separator className="my-1 h-px bg-hairline" />
          {n > 1 && (
            <Menu.Item className={item} onSelect={() => dispatch({ type: "show-all" })}>
              {t("layout.hidden.showAll")}
            </Menu.Item>
          )}
          <Menu.Item className={item} onSelect={() => dispatch({ type: "reset" })}>
            {t("layout.reset")}
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}

function nameOf(h: HiddenItem, t: TFunc): string {
  return h.kind === "region" ? t(`layout.region.${h.id}`) : t(`layout.panel.${h.id}`);
}
