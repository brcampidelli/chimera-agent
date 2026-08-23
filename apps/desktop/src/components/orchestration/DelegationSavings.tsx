import { useQuery } from "@tanstack/react-query";
import { Split } from "lucide-react";

import { getDelegations } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * What splitting the work actually saved, measured against what one agent would have cost.
 *
 * `getDelegations` had no caller anywhere in the app. The receipts were written, summarised and
 * served, and the one question they answer — was the fan-out worth paying for — had no surface.
 *
 * Three refusals make this readable rather than flattering, and they are the feature:
 *
 * - **Dollars only when they were measured.** `usd_saving` needs a counterfactual AND a paired
 *   measurement. Without both this shows tokens and says the cost is unknown. Printing $0.00 there
 *   would make the runs nobody could price look like the free ones.
 * - **The counterfactual is an ESTIMATE and says so.** Nobody ran the single-agent version; its
 *   cost is modelled. A saving quoted against a number that was never observed is a projection,
 *   and calling it a measurement is how a benchmark stops being worth anything.
 * - **How many were estimated is on screen**, not folded into the total.
 * - **A loss is not printed as a saving.** `token_saving` is `counterfactual − measured`: a SIGNED
 *   net, negative whenever the fan-out cost more than doing it inline. This printed it verbatim
 *   into "{n} tokens saved", so a real screen read "-12,961 tokens saved", and the dollar line was
 *   `text-ok` green either way. The two halves can also disagree honestly — more tokens on cheaper
 *   models is fewer dollars, which is the whole point of routing by role — so they are decided
 *   separately rather than from one verdict.
 */
export function DelegationSavings() {
  const t = useT();
  const { data } = useQuery({ queryKey: ["delegations"], queryFn: getDelegations });
  const summary = data?.summary;

  if (!summary || !summary.n) {
    return null; // Nothing delegated yet: an empty panel would be a question nobody asked.
  }

  const priced = summary.usd_saving !== null && summary.usd_saving !== undefined;
  // Split before formatting, and `Math.abs` before the sentence: the minus sign belongs to the
  // wording, not to a number sitting inside a phrase that already says "saved".
  const tokens = summary.token_saving ?? 0;
  const usd = summary.usd_saving ?? 0;
  return (
    <section className="space-y-1.5 border-t border-hairline p-3">
      <div className="flex items-center gap-2 text-accent">
        <Split className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">{t("orch.saving.title")}</h2>
      </div>

      <p className={tokens < 0 ? "text-xs text-warn-foreground" : "text-xs text-foreground"}>
        {t(tokens < 0 ? "orch.saving.tokensMore" : "orch.saving.tokens", {
          n: Math.abs(tokens).toLocaleString(),
          runs: summary.n,
        })}
      </p>

      {priced ? (
        <p className={usd < 0 ? "text-xs tabular-nums text-warn-foreground" : "text-xs tabular-nums text-ok-foreground"}>
          {t(usd < 0 ? "orch.saving.usdMore" : "orch.saving.usd", { usd: Math.abs(usd).toFixed(4) })}
        </p>
      ) : (
        <p className="text-xs text-warn-foreground">
          {t("orch.saving.unpriced", { priced: summary.priced_n ?? 0, n: summary.n })}
        </p>
      )}

      {summary.estimated_n ? (
        <p className="text-xs text-muted-foreground">
          {t("orch.saving.estimated", { n: summary.estimated_n, total: summary.n })}
        </p>
      ) : null}
    </section>
  );
}
