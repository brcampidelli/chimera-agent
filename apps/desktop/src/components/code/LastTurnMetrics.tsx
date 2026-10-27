import type { CodeTurnDone } from "@/lib/api";
import { useNum, useT } from "@/lib/i18n";

/** Steps, tokens and cost of the turn that just finished, for the conversation header.
 *
 *  Renders nothing for a turn that ran no steps: a turn that only handed work to the background (or
 *  answered from memory) has no spend of its own to report, and "0 steps · 0 tokens" would read as a
 *  free, empty run. The cost uses the receipt's format (`$0.0000`, "price unknown" when the model's
 *  price is not known) so the same turn reads the same in both places. */
export function LastTurnMetrics({ done }: { done: CodeTurnDone | null | undefined }) {
  const t = useT();
  const num = useNum();
  if (!done || !done.steps || done.steps <= 0) return null;
  const tokens = done.prompt_tokens + done.completion_tokens;
  const cost = done.usd === null ? t("code.chat.unknownCost") : `$${done.usd.toFixed(4)}`;
  return (
    <span className="text-xs text-muted-foreground" data-testid="last-turn-metrics">
      {t("code.chat.steps", { n: num(done.steps) })} · {t("code.chat.tokenCount", { n: num(tokens) })} · {cost}
    </span>
  );
}
