import { useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { previewHierarchy, type HierarchyRunInput } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { HierarchyPreview } from "@/lib/types";

import { HierarchyRun } from "./HierarchyRun";
import { PlanPreview } from "./PlanPreview";

/**
 * Orchestration: describe a task, see what the orchestrator would do with it, then run it.
 *
 * The order is the point. "See the plan" is the primary action and the cheap one; running is a
 * second, deliberate click. That matters more here than on most screens, because the commonest
 * answer to "what would you do" is *one agent, and here is why* — and finding that out should not
 * cost a fan-out.
 */
export function Orchestration({
  workspace,
  onOpenCode,
}: {
  workspace: string;
  onOpenCode: () => void;
}) {
  const t = useT();
  const [task, setTask] = useState("");
  const [plan, setPlan] = useState<HierarchyPreview | null>(null);
  const [planning, setPlanning] = useState(false);
  const [error, setError] = useState("");
  // A confirmed run, keyed by when it was confirmed: handing HierarchyRun a new key makes a new
  // run a new component instead of a re-run of the last one.
  const [run, setRun] = useState<{ at: number; request: HierarchyRunInput } | null>(null);

  async function seePlan() {
    if (!task.trim()) return;
    setPlanning(true);
    setError("");
    setRun(null);
    // Cleared before the request, not after it. Leaving the previous plan up while a new one
    // loads shows a decomposition for the PREVIOUS task next to the current text — and the
    // decompose call takes long enough for someone to read it and act on it.
    setPlan(null);
    try {
      setPlan(await previewHierarchy({ task }));
    } catch (err) {
      setPlan(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanning(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <div>
          <label
            className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            htmlFor="orch-task"
          >
            {t("orch.task.label")}
          </label>
          <textarea
            id="orch-task"
            value={task}
            onChange={(event) => setTask(event.target.value)}
            rows={3}
            placeholder={t("orch.task.placeholder")}
            className="mt-1 w-full resize-y rounded-card border border-hairline bg-surface-2/40 p-3 text-sm text-foreground placeholder:text-muted-foreground"
          />
        </div>

        {/* The project comes from where a project is chosen, and is shown rather than asked for.
            A second folder field would be a second answer to one question — the same argument the
            batch board already settled. */}
        <p className="truncate font-mono text-xs text-muted-foreground" title={workspace}>
          {workspace || t("code.sessions.defaultProject")}
        </p>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => void seePlan()} disabled={!task.trim() || planning}>
            {planning ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("orch.preview")}
          </Button>
          {/* Precise about its own cost, because the honest sentence and the flattering one differ:
              on the fan-out branch the top model really is called to split the task. What the
              preview never spends is WORKER tokens. */}
          <span className="text-xs text-muted-foreground">{t("orch.previewCost")}</span>
        </div>

        {error ? <p className="text-xs text-bad">{error}</p> : null}
      </div>

      {plan && !run ? (
        <PlanPreview
          plan={plan}
          running={planning}
          onOpenCode={onOpenCode}
          onRun={() => setRun({ at: Date.now(), request: { task } })}
        />
      ) : null}

      {run ? <HierarchyRun key={run.at} request={run.request} onOpenCode={onOpenCode} /> : null}
    </div>
  );
}
