import { CornerDownLeft } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { useT } from "@/lib/i18n";
import type { Suggestion } from "@/lib/suggestions";
import { cn } from "@/lib/utils";

/**
 * The suggested next steps under the last answer, as chips that fill the box (study 29, P4.5).
 *
 * Styled like the empty screen's examples on purpose: they are the same kind of thing — words
 * offered for the person to read and change — and a click on either puts text in the box and sends
 * nothing. Renders nothing when there is nothing to suggest, so a turn with no open fact to act on
 * looks exactly as it did before.
 */
export function TurnSuggestions({
  items,
  onPick,
}: {
  items: Suggestion[];
  onPick: (item: Suggestion) => void;
}) {
  const t = useT();
  if (items.length === 0) return null;
  return (
    <div
      role="group"
      aria-label={t("code.suggest.label")}
      className="flex flex-wrap items-center gap-2"
      data-testid="turn-suggestions"
    >
      {items.map((item) => (
        <Tooltip key={item.kind} label={t("code.suggest.hint")} side="top">
          <button
            type="button"
            className={cn(
              "flex max-w-full items-center gap-1.5 rounded-chip border border-hairline bg-surface-2 px-3 py-1.5 text-left text-xs",
              "text-muted-foreground transition-colors duration-1 ease-out hover:border-accent hover:text-foreground",
              focusRing,
            )}
            onClick={() => onPick(item)}
          >
            <CornerDownLeft className="h-3 w-3 shrink-0" aria-hidden />
            <span className="truncate">{item.label}</span>
          </button>
        </Tooltip>
      ))}
    </div>
  );
}
