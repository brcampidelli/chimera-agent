import { TriangleAlert } from "lucide-react";
import { useT } from "@/lib/i18n";

/** A warning the agent's run sent without stopping: a limit is near, or something was done to keep going. */
export interface NoticeEntry {
  code: string;
  text: string;
  /** What the frame carried besides the words (an amount, a model), for the codes that show one. */
  data?: Record<string, unknown>;
}

/**
 * The turn's warnings, one line each.
 *
 * Not a stop and not a card: nothing here waits for the person. The words come from the frame's own
 * `text` only for a code this build does not know, so an older screen still tells a newer server's
 * warning instead of dropping it. Known codes are written out rather than composed, for the same
 * reason as `TodoPanel`: the dead-key gate cannot see an interpolated key.
 */
export function NoticeList({ items }: { items?: NoticeEntry[] }) {
  const t = useT();
  if (!items?.length) return null;
  const words = (n: NoticeEntry) =>
    n.code === "steps_low"
      ? t("code.notice.stepsLow")
      : n.code === "compacted"
        ? t("code.notice.compacted")
        : n.code === "tool_loop_warn"
          ? t("code.notice.toolLoopWarn")
          : n.code === "price_unknown"
            ? t("code.notice.priceUnknown", { model: String(n.data?.model ?? "") })
            : n.code === "spend_warn"
              ? t("code.notice.spendWarn", { usd: Number(n.data?.usd ?? 0).toFixed(2) })
              : n.text;
  return (
    <ul className="space-y-0.5" role="status">
      {items.map((n) => (
        <li key={n.code} className="flex items-start gap-2 text-xs text-warn-foreground">
          <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
          <span>{words(n)}</span>
        </li>
      ))}
    </ul>
  );
}
