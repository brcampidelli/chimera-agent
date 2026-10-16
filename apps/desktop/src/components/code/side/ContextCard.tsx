import { contextPercent, useConversationSide } from "@/lib/conversation-side";
import { useNum, useT } from "@/lib/i18n";

/**
 * How full the model's window is (C5).
 *
 * While a turn runs, its live context frame — the same reading the meter beside the turn's timer
 * shows, with the same arithmetic (`contextPercent`). With no turn running, the peak prompt of the
 * last receipt, labelled as such: a number from the past turn read as the present one would say the
 * conversation is as full as it was at its fullest. With no window known (a reopened conversation,
 * or a route that never said), the tokens alone: a percentage of a guessed window would read as
 * measured.
 */
export function ContextCard() {
  const t = useT();
  const num = useNum();
  const side = useConversationSide();
  if (!side) return null;
  const reading = side.live ?? side.peak;
  if (!reading) return <p className="text-sm text-muted-foreground">{t("side.context.none")}</p>;
  const percent = contextPercent(reading.used, reading.window);
  const source = side.live ? t("side.context.live") : t("side.context.peak");
  const label =
    percent === null ? t("code.chat.contextTokens", { n: num(reading.used) }) : t("code.chat.contextPercent", { n: percent });
  return (
    <div className="space-y-1.5" data-testid="side-context">
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <span className="tabular-nums">{label}</span>
        <span className="text-xs text-muted-foreground">{source}</span>
      </div>
      {percent === null ? null : (
        <div
          role="progressbar"
          aria-label={label}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          className="h-1.5 overflow-hidden rounded-full bg-surface-2"
        >
          {/* The share is a measurement, not a design value: an inline width, like the meter's. */}
          <span className="block h-full bg-accent" style={{ width: `${percent}%` }} />
        </div>
      )}
      {percent === null ? null : (
        <p className="text-xs tabular-nums text-muted-foreground">
          {t("side.context.ofWindow", { used: num(reading.used), window: num(reading.window ?? 0) })}
        </p>
      )}
    </div>
  );
}
