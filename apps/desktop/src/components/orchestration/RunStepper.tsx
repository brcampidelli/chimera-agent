import { useT } from "@/lib/i18n";
import type { OrchestrationState } from "@/lib/orchestration-run";
import { cn } from "@/lib/utils";

const ORDER = ["classified", "decomposed", "working", "synthesizing", "done"] as const;

/**
 * Where the run is, in the four stages it always passes through in the same order.
 *
 * Announced through one polite live region rather than per-stage, so a screen reader hears "four
 * workers running" once instead of a chip at a time. The worker cards below are NOT in a live
 * region: four cards changing at once would read as an interruption every time a worker finishes.
 */
export function RunStepper({ state }: { state: OrchestrationState }) {
  const t = useT();
  const reached = ORDER.indexOf(state.stage as (typeof ORDER)[number]);

  return (
    <ol className="flex flex-wrap items-center gap-1.5" role="status" aria-live="polite">
      {ORDER.map((stage, index) => {
        const done = reached > index;
        const current = reached === index;
        return (
          <li
            key={stage}
            className={cn(
              "rounded-chip px-2 py-0.5 text-xs transition-colors duration-200 ease-out",
              current
                ? "bg-accent/15 text-accent ring-1 ring-accent/25"
                : done
                  ? "bg-surface-2 text-muted-foreground ring-1 ring-hairline"
                  : "text-muted-foreground/60",
            )}
            aria-current={current ? "step" : undefined}
          >
            {stage === "working" && state.workers.length > 0
              ? t("orch.stage.workingN", { n: state.workers.length })
              : t(`orch.stage.${stage}`)}
          </li>
        );
      })}
    </ol>
  );
}
