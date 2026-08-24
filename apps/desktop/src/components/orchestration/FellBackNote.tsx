import { User } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/**
 * The orchestrator chose the single-agent path — the most common outcome by a wide margin, and the
 * one this screen is most able to get wrong.
 *
 * `classify_task` routes anything containing write intent (implement, fix, refactor, create, and
 * their Portuguese equivalents) to `sequential_write`, where fanning out measures worse. In a
 * coding tool that is most of what anybody types. So this is not an error state, and three rules
 * follow from that and are enforced by a test:
 *
 *  - no `role="alert"`: assistive tech must not announce a normal decision as a problem;
 *  - the accent tone, never `warn` or `bad`;
 *  - the sentence is an assertion about the task, not an apology about the feature.
 *
 * The same component renders the prediction (from the preview) and the fact (from the `fell_back`
 * frame), so the two can never word it differently.
 */
export function FellBackNote({
  shape,
  reason,
  sources,
  onRun,
  onOpenCode,
  onCrew,
  running,
}: {
  shape: string;
  reason: string;
  /** Distinct sources NAMED IN THE TASK TEXT — `count_sources`, which never opens the folder. */
  sources?: number;
  /** Absent once the run is under way — by then it is a report, not an offer. */
  onRun?: () => void;
  onOpenCode?: () => void;
  /** Offered for write-shaped work only. The hierarchy is right to refuse this task; a crew is
   *  the shape that fits it — same task, several attempts, a check decides which one lands. */
  onCrew?: () => void;
  running?: boolean;
}) {
  const t = useT();
  // Keyed off the machine-readable code, never the backend's prose. The reason string is a log
  // line and has been reworded before; a UI matching on its text breaks when someone improves it.
  const why =
    reason === "unprofitable"
      ? t("orch.fellback.unprofitable")
      : reason === "decompose_failed"
        ? t("orch.fellback.decomposeFailed")
        : reason === "workers_failed"
          ? t("orch.fellback.workersFailed")
          : shape === "sequential_write"
            ? t("orch.fellback.write")
            : t("orch.fellback.simple");

  // "This task is short and direct" is an assertion about the TASK, and for somebody who wrote
  // "compare the two reports in this folder" it is simply false: the task is neither, and what was
  // short was what the rule could see. `count_sources` reads the wording and nothing else — by
  // design, since classification is deterministic and never an LLM call — so a divisible task
  // whose files were not named lands here with a sentence that blames the task.
  //
  // Only when nothing was named. With two or more sources counted, the rule DID see them and
  // declined on size, and repeating "name them" would be advice they already followed.
  const nameThem =
    (reason === "unprofitable" || (!reason && shape !== "sequential_write")) && (sources ?? 0) < 2;

  return (
    <div className="rounded-card border border-accent/25 bg-accent/5 p-4">
      <div className="flex items-center gap-2 text-accent">
        <User className="h-4 w-4" />
        <h3 className="text-sm font-semibold">{t("orch.fellback.title")}</h3>
      </div>
      <p className="mt-2 text-sm text-muted-foreground">{why}</p>
      {nameThem ? (
        <p className="mt-1.5 text-sm text-muted-foreground">{t("orch.fellback.nameThem")}</p>
      ) : null}
      {onRun || onOpenCode ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {/* First, because it is the answer to what was actually asked. A task that edits files
              is not a task the hierarchy should run — but it IS a task a crew is built for, and
              until this button existed the screen's reply to the commonest request was a note
              pointing somewhere else. */}
          {onCrew && shape === "sequential_write" ? (
            <Button size="sm" onClick={onCrew} disabled={running}>
              {t("orch.fellback.buildCrew")}
            </Button>
          ) : null}
          {onRun ? (
            <Button
              size="sm"
              variant={onCrew && shape === "sequential_write" ? "outline" : "primary"}
              onClick={onRun}
              disabled={running}
            >
              {t("orch.fellback.runSingle")}
            </Button>
          ) : null}
          {/* Only for write-shaped tasks, and it is the most useful thing on the screen when it
              appears: the classifier has just said "this is writing work", and the Code screen is
              the tool for writing work. Without it the commonest outcome ends in a dead end. */}
          {onOpenCode && shape === "sequential_write" ? (
            <Button size="sm" variant="outline" onClick={onOpenCode}>
              {t("orch.fellback.openCode")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
