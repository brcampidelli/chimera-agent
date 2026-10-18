import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { CatalogStatus } from "@/components/catalog/model";

/** Dot and label per state. Colour never stands alone: the word is always there, and the label
 *  takes the `-foreground` ink, because the plain state tokens fall under 4.5:1 as 11px text on the
 *  light theme. */
const LOOK: Record<CatalogStatus, { key: string; dot: string; ink: string }> = {
  pending: { key: "catalogUi.status.pending", dot: "bg-warn", ink: "text-warn-foreground" },
  active: { key: "catalogUi.status.active", dot: "bg-ok", ink: "text-ok-foreground" },
  held: { key: "catalogUi.status.held", dot: "bg-warn", ink: "text-warn-foreground" },
  inactive: { key: "catalogUi.status.inactive", dot: "bg-muted-foreground", ink: "text-muted-foreground" },
  denied: { key: "catalogUi.status.denied", dot: "bg-bad", ink: "text-bad-foreground" },
  learned: { key: "catalogUi.status.learned", dot: "bg-accent", ink: "text-accent-ink" },
};

export function StatusPill({ status, id }: { status: CatalogStatus; id?: string }) {
  const t = useT();
  const look = LOOK[status];
  return (
    <span id={id} data-status={status} className={cn("inline-flex items-center gap-1.5 text-xs", look.ink)}>
      <span aria-hidden className={cn("h-1.5 w-1.5 shrink-0 rounded-full", look.dot)} />
      {t(look.key)}
    </span>
  );
}
