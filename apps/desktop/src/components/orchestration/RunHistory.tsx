import { useQuery } from "@tanstack/react-query";
import { History, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/panel";
import { focusRing } from "@/components/ui/focus";
import { getOrchestrationFrames, getOrchestrationRuns, type OrchFrame } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The runs that are on disk, and a way back into them.
 *
 * The transcripts have been persisted since rc11 and the endpoint that lists them had no caller at
 * all — not in a component, not in a test. What the screen could reach was one id in localStorage,
 * so it resumed the LAST run, from the machine that started it, and every run before that sat
 * intact on disk and unreachable. A fan-out costs a top-model decompose, N workers and a synthesis;
 * "we kept the receipt where you cannot see it" is not meaningfully different from not keeping it.
 *
 * Opening one REPLAYS it. Nothing is re-run: the frames go through the same reducer the live
 * stream feeds, so a past run renders exactly as it did while it was happening, and costs nothing
 * to look at.
 */
export function RunHistory({
  onOpen,
}: {
  onOpen: (run: { runId: string; kind: string; frames: OrchFrame[] }) => void;
}) {
  const t = useT();
  const { data, isLoading } = useQuery({
    queryKey: ["orchestration-runs"],
    queryFn: getOrchestrationRuns,
    // Cheap and worth being current: a run that just finished should not need a reload to appear.
    refetchInterval: 20_000,
  });

  const runs = data?.runs ?? [];
  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      </p>
    );
  }

  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        <History className="h-3.5 w-3.5" />
        {t("orch.history.title")}
      </h3>

      {runs.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("orch.history.empty")}</p>
      ) : (
        <ul className="space-y-1">
          {runs.map((run) => (
            <li key={run.run_id}>
              <button
                type="button"
                className={cn(
                  "flex w-full items-center gap-2 rounded-card border border-hairline bg-surface-2/40 px-3 py-2 text-left transition-colors duration-1 ease-out hover:bg-surface-2",
                  focusRing,
                )}
                onClick={() => {
                  void getOrchestrationFrames(run.run_id).then(({ frames }) =>
                    onOpen({ runId: run.run_id, kind: run.kind, frames }),
                  );
                }}
              >
                <span className="min-w-0 flex-1 truncate text-xs text-foreground" title={run.task}>
                  {run.task}
                </span>
                <span className="shrink-0 font-mono text-xs text-muted-foreground">
                  {t("orch.history.frames", { n: run.frames })}
                </span>
                {/* Three states, not two. `done: false` used to cover both "still working" and
                    "the process that was running this is gone", and one measured run sat in the
                    second for twenty-two minutes looking exactly like the first. */}
                {run.done ? null : run.orphaned ? (
                  <Badge tone="bad">{t("orch.history.orphaned")}</Badge>
                ) : (
                  <Badge tone="accent">{t("orch.history.running")}</Badge>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
