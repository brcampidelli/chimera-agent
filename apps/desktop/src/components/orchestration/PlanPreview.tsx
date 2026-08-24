import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { HierarchyPreview } from "@/lib/types";

import { FellBackNote } from "./FellBackNote";

/**
 * What the orchestrator would do, shown before anything is spent on workers.
 *
 * The same job `BatchProposal` does on the Code screen — confirm a split before paying for it —
 * and written separately rather than extended, because the payloads share no field. What is
 * copied is the shape of the decision: the cheap action is the default, and the expensive one is
 * a deliberate second click.
 */
export function PlanPreview({
  plan,
  onRun,
  onOpenCode,
  onCrew,
  running,
}: {
  plan: HierarchyPreview;
  onRun: () => void;
  onOpenCode: () => void;
  onCrew: () => void;
  running: boolean;
}) {
  const t = useT();

  if (plan.would_fall_back) {
    return (
      <FellBackNote
        shape={plan.shape}
        reason={plan.fell_back_reason}
        sources={plan.sources}
        onRun={onRun}
        onOpenCode={onOpenCode}
        onCrew={onCrew}
        running={running}
      />
    );
  }

  return (
    <div className="rounded-card border border-hairline bg-surface-2/40 p-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="accent">{t("orch.plan.parallel")}</Badge>
        {plan.sources >= 2 ? <Badge>{t("orch.plan.sources", { n: plan.sources })}</Badge> : null}
        {/* Not a blocker: the orchestrator itself waives this estimate when two or more distinct
            sources are named, because that is the region where the token win is measured rather
            than guessed. Shown so the reasoning is visible, never to stop the run. */}
        {plan.profitable_estimate ? null : <Badge tone="warn">{t("orch.plan.thin")}</Badge>}
      </div>

      <ol className="mt-3 space-y-1.5">
        {/* `?? []` because the generated type marks every defaulted field optional: the backend
            always sends the key, the schema just cannot say so. */}
        {(plan.subtasks ?? []).map((objective, index) => (
          <li key={objective} className="flex gap-2 text-sm text-foreground">
            <span className="tabular-nums text-muted-foreground">{index + 1}.</span>
            <span className="min-w-0 flex-1">{objective}</span>
          </li>
        ))}
      </ol>

      <p className="mt-3 text-xs text-muted-foreground">
        {t("orch.plan.budget", { n: plan.workers, tokens: plan.budget_per_worker })}
      </p>

      <div className="mt-3">
        {/* No count on the button. The number is on the line above it, and a label carrying a
            figure has to agree with it — which `t()` cannot do: it interpolates and nothing more,
            so "1 trabalhadores" was what a single-worker plan actually rendered. Moving every
            count into a "label: n" position fixes it in all ten languages at once, including the
            two with three plural forms that a naive one/other split would still get wrong. */}
        <Button size="sm" onClick={onRun} disabled={running}>
          {t("orch.plan.run")}
        </Button>
      </div>
    </div>
  );
}
