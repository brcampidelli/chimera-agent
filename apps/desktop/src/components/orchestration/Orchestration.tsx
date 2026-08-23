import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  previewHierarchy,
  type CrewRunInput,
  type HierarchyRunInput,
  type OrchFrame,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { HierarchyPreview } from "@/lib/types";

import { CrewForm } from "./CrewForm";
import { CrewRun } from "./CrewRun";
import { HierarchyRun } from "./HierarchyRun";
import { forgetRun, resumeFrames } from "./resume";
import { PlanPreview } from "./PlanPreview";
import { RunHistory } from "./RunHistory";

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
  // Two states, not one: the form is open (you are choosing the crew) or a crew is running.
  // Collapsing them would make the form vanish the instant Run is pressed, taking the roles you
  // just wrote with it — and that is exactly the state you want back when the check turns out
  // to be the thing that was wrong.
  const [crewOpen, setCrewOpen] = useState(false);
  // The last run this browser started, read back from the server's transcript. A fan-out costs a
  // top-model decompose, N workers and a synthesis, and until these were persisted, closing the tab
  // threw the answer away and kept the bill.
  const [resumed, setResumed] = useState<OrchFrame[] | null>(null);
  const [crew, setCrew] = useState<{ at: number; request: CrewRunInput } | null>(null);
  // A run opened from the history. Keyed by its own id so opening a second one replaces the first
  // rather than replaying into a component that already holds another run's frames.
  const [opened, setOpened] = useState<{ runId: string; kind: string; frames: OrchFrame[] } | null>(
    null,
  );

  useEffect(() => {
    // Once, on mount. A run started in THIS session is already on screen; this is for the one that
    // was not — the tab that was closed, the reload, the connection that dropped.
    void resumeFrames().then(({ frames }) => {
      if (frames.length > 0) setResumed(frames);
    });
  }, []);

  async function seePlan() {
    if (!task.trim()) return;
    setPlanning(true);
    setError("");
    setRun(null);
    setCrew(null);
    setCrewOpen(false);
    // Asking for a new plan is saying the old run is done with. Leaving it on screen under a fresh
    // plan would put two runs in one column with nothing saying which is which.
    setResumed(null);
    setOpened(null);
    forgetRun();
    // Cleared before the request, not after it. Leaving the previous plan up while a new one
    // loads shows a decomposition for the PREVIOUS task next to the current text — and the
    // decompose call takes long enough for someone to read it and act on it.
    setPlan(null);
    try {
      setPlan(await previewHierarchy({ task, workspace }));
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

        {error ? <p className="text-xs text-bad-foreground">{error}</p> : null}
      </div>

      {plan && !run && !crew ? (
        <PlanPreview
          plan={plan}
          running={planning}
          onOpenCode={onOpenCode}
          onCrew={() => setCrewOpen(true)}
          // The id travels with the run, so what executes is the split shown above rather than a
          // fresh one. Without it the approval on this screen would be approval of nothing.
          onRun={() =>
            setRun({ at: Date.now(), request: { task, workspace, plan_id: plan.plan_id } })
          }
        />
      ) : null}

      {crewOpen && !crew ? (
        <CrewForm
          running={planning}
          onRun={(workers, verify) =>
            setCrew({
              at: Date.now(),
              request: { task, workspace, workers, verify: verify || null },
            })
          }
        />
      ) : null}

      {run ? <HierarchyRun key={run.at} request={run.request} onOpenCode={onOpenCode} /> : null}
      {/* Only when nothing is running here: a live run is the thing on screen, and a replayed one
          beside it would be two answers to one question. */}
      {!run && !crew && resumed ? (
        <HierarchyRun key="resumed" resume={resumed} onOpenCode={onOpenCode} />
      ) : null}
      {crew ? <CrewRun key={crew.at} request={crew.request} /> : null}

      {/* A run opened from the list, replayed. Both kinds, because both were being written to disk
          and only one of them could ever be read back. */}
      {opened ? (
        opened.kind === "crew" ? (
          <CrewRun key={opened.runId} resume={opened.frames} />
        ) : (
          <HierarchyRun key={opened.runId} resume={opened.frames} onOpenCode={onOpenCode} />
        )
      ) : null}

      {/* Last, because it is the archive and not the work. Hidden while something is live on this
          screen: two runs in one column with nothing saying which is which is the confusion the
          resume path already avoids. */}
      {!run && !crew ? (
        <RunHistory
          onOpen={(picked) => {
            setResumed(null);
            setOpened(picked);
          }}
        />
      ) : null}
    </div>
  );
}
