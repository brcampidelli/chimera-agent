import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, Loader2, Play, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { Switch } from "@/components/ui/switch";
import { PausedRunCard } from "@/components/run/PausedRunCard";
import { focusRing } from "@/components/ui/focus";
import { NO_OVERRIDE, RolesBar, toRoleModels, type RoleOverride } from "@/components/code/RolesBar";
import { getPlan, getPausedRuns, type Profile, type RunEvent } from "@/lib/api";
import { useRunSession } from "@/lib/run-session";
import { useT, type TFunc } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const fieldCls = "field w-full px-3 text-sm";

/**
 * Start an autonomous run.
 *
 * There were three of these: one in Runs, one inside Code's panel, one in Agents. All doing the
 * same thing — a task, an optional verify command, an attempt budget — and all drifting apart,
 * because the only thing holding them in sync was whoever remembered to edit all three.
 *
 * The form is local; the RUN is not. It lives in the shell's run session, so leaving this screen
 * no longer abandons it: come back and the progress is still here, and the Stop below is the same
 * Stop the status bar offers from every other screen.
 */
export function RunLauncher({
  variant = "panel",
  workspace,
}: {
  variant?: "panel" | "inline";
  /** Fixed workspace. When given, the field is hidden — Code already knows where it is working. */
  workspace?: string;
}) {
  const t = useT();
  const qc = useQueryClient();
  const run = useRunSession();
  const [task, setTask] = useState("");
  const [verify, setVerify] = useState("");
  // The plan the user has read, and possibly rewritten. Empty means "plan for yourself", which is
  // what every run did before this and still does when nobody asks to see it first.
  const [plan, setPlan] = useState("");
  const [planning, setPlanning] = useState(false);
  const [planNote, setPlanNote] = useState("");
  // Whether the panel is open, which is NOT the same as whether it has text in it. Deriving the
  // panel from the content meant that clearing the box to rewrite the plan made the box disappear
  // mid-edit — found by a test that tried to do exactly that. Opening is asking; closing is
  // Discard.
  const [planOpen, setPlanOpen] = useState(false);
  const [ws, setWs] = useState("");
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [pauseOnTaint, setPauseOnTaint] = useState(false);
  // Who does what. `touched` is not decoration: `worth.py` groups evidence by
  // (profile, profile_source), so a run that got the default and a run somebody deliberately set
  // to "max" must not be counted as the same thing. Sending `profile_source: "user"` for a form
  // nobody touched would fabricate exactly that.
  const [profile, setProfile] = useState<Profile>("balanced");
  const [roles, setRoles] = useState<RoleOverride>(NO_OVERRIDE);
  const [oneModel, setOneModel] = useState(false);
  const [touched, setTouched] = useState(false);
  // Runs parked before this window opened. A pause outlives the stream that reported it, so the
  // only way to see one you did not personally witness is to ask.
  const parked = useQuery({ queryKey: ["runs", "paused"], queryFn: getPausedRuns });

  const running = run.running;
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["runs"] });
  };

  /** The run request for a thread, so start and resume cannot drift apart. */
  const request = (threadId: string | null) => ({
    task: task.trim(),
    verify: verify.trim() || null,
    workspace: workspace ?? (ws.trim() || null),
    max_attempts: maxAttempts,
    thread_id: threadId,
    pause_on_taint: pauseOnTaint,
    // `/api/runs` has always accepted both — `app.py` routes plan, edit and review off them — and
    // this screen sent neither, so every run here took the built-in tiers no matter what.
    profile,
    roles: toRoleModels(roles),
    profile_source: touched ? "user" : "system",
    // The plan the user approved, verbatim. `RunRequest.plan` makes the run follow these exact
    // steps and skip planning entirely — so a correction made here is a correction the run cannot
    // undo by re-planning around it.
    plan: plan.trim() || null,
  });

  function start() {
    if (!task.trim() || running) return;
    // A thread only when the run can actually pause: without one there is nowhere to park it, and
    // an unthreaded run is the cheaper, unchanged path.
    const threadId = pauseOnTaint ? `run-${Date.now().toString(36)}` : null;
    run.start(request(threadId), { onDone: invalidate });
  }

  /** Ask what it intends to do, before it does any of it.
   *
   *  One tool-free model call. Nothing is written, nothing is run, and the steps come back as text
   *  the user can rewrite — which is the only moment in a run where a correction costs nothing.
   *  After the first edit the whole run follows the human's version.
   */
  async function preview() {
    if (!task.trim() || planning || running) return;
    setPlanning(true);
    setPlanNote("");
    setPlanOpen(true);
    try {
      const p = await getPlan(task.trim(), workspace ?? (ws.trim() || null));
      setPlan(p.text || "");
      // The endpoint degrades to an empty plan with a note rather than failing, so an empty answer
      // needs a sentence: a blank box would read as "it plans to do nothing".
      if (!p.text) setPlanNote(p.note || t("runs.plan.empty"));
    } catch {
      setPlanNote(t("runs.plan.failed"));
    } finally {
      setPlanning(false);
    }
  }

  /** Carry out a recorded verdict. Recording it did not conclude the run — this does. */
  function resume(threadId: string) {
    run.clearPaused();
    // Keeps pause_on_taint armed: for accept/edit the resume finalizes without re-running, so the
    // flag is moot, but "respond" makes a fresh attempt that can read untrusted content again and
    // must be able to stop and ask a second time.
    run.start(request(threadId), {
      onDone: () => {
        invalidate();
        void qc.invalidateQueries({ queryKey: ["runs", "paused"] });
      },
    });
  }

  // Formatted here rather than stored formatted: the session holds raw events so the transcript
  // follows the current language instead of freezing in whichever one was active at the time.
  const lines = run.events.map((e) => liveLine(e, t)).filter((l): l is string => l !== null);
  if (run.done) lines.push(run.done.success ? t("runs.doneOk") : t("runs.doneFail"));
  else if (run.broken) lines.push(t("runs.doneFail"));

  const body = (
    <div className={cn("space-y-2.5", variant === "panel" && "px-4 py-3")}>
      <textarea
        className={cn(fieldCls, "min-h-[72px] resize-y py-2")}
        placeholder={t("runs.taskPlaceholder")}
        aria-label={t("runs.taskPlaceholder")}
        value={task}
        onChange={(e) => setTask(e.target.value)}
        disabled={running}
      />
      {planOpen ? (
        <div className="space-y-1.5 rounded-chip border border-accent/40 bg-accent/5 p-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium">{t("runs.plan.title")}</span>
            <button
              type="button"
              className={cn("text-xs text-muted-foreground hover:text-foreground", focusRing)}
              onClick={() => {
                setPlan("");
                setPlanNote("");
                setPlanOpen(false);
              }}
              disabled={running}
            >
              {t("runs.plan.discard")}
            </button>
          </div>
          {planNote ? <p className="text-xs text-warn-foreground">{planNote}</p> : null}
          {/* Editable, which is the entire point. A plan you can only approve is a plan you can
              only agree with, and the person reading it is the one who knows what was left out. */}
          <textarea
            className={cn(fieldCls, "min-h-[96px] resize-y py-2 font-mono text-xs")}
            aria-label={t("runs.plan.title")}
            value={plan}
            onChange={(e) => setPlan(e.target.value)}
            disabled={running}
          />
          <p className="text-xs text-muted-foreground">{t("runs.plan.hint")}</p>
        </div>
      ) : null}
      <input
        className={cn(fieldCls, "h-9 font-mono text-xs")}
        placeholder={t("runs.verifyPlaceholder")}
        aria-label={t("runs.verifyPlaceholder")}
        value={verify}
        onChange={(e) => setVerify(e.target.value)}
        disabled={running}
      />
      {workspace === undefined && (
        <input
          className={cn(fieldCls, "h-9 font-mono text-xs")}
          placeholder={t("runs.workspacePlaceholder")}
          aria-label={t("runs.workspacePlaceholder")}
          value={ws}
          onChange={(e) => setWs(e.target.value)}
          disabled={running}
        />
      )}
      {/* Beside the attempt budget rather than behind a settings screen: which model writes the
          patch is a property of THIS run, and the receipt it produces is grouped by it. */}
      <div className="border-t border-hairline pt-3">
        <RolesBar
          profile={profile}
          onProfile={(p) => {
            setProfile(p);
            setTouched(true);
          }}
          override={roles}
          onOverride={(o) => {
            setRoles(o);
            setTouched(true);
          }}
          oneModel={oneModel}
          onOneModel={setOneModel}
          disabled={running}
        />
      </div>
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {t("runs.maxAttempts")}
          <input
            type="number"
            min={1}
            max={10}
            className="field h-9 w-16 px-2 text-sm"
            value={maxAttempts}
            onChange={(e) => setMaxAttempts(Math.min(10, Math.max(1, Number(e.target.value) || 1)))}
            disabled={running}
          />
        </label>
        {/* Beside Run rather than instead of it. Someone who knows what they want should not have
            to click twice, and someone who does not should not have to find out by watching files
            change. The label says what it costs: one call, no tools, nothing written. */}
        <Button
          size="sm"
          variant="outline"
          disabled={!task.trim() || running || planning}
          onClick={() => void preview()}
        >
          {planning ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> {t("runs.plan.working")}
            </>
          ) : (
            <>
              <ListChecks className="h-4 w-4" /> {t("runs.plan.show")}
            </>
          )}
        </Button>
        <Button size="sm" disabled={!task.trim() || running} onClick={start}>
          {running ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> {t("runs.running")}
            </>
          ) : (
            <>
              <Play className="h-4 w-4" /> {t("runs.run")}
            </>
          )}
        </Button>
        {running && (
          <button
            type="button"
            onClick={run.stop}
            // Disabled until the backend has handed us the run's id: there is nothing to address a
            // cancel to before the first frame arrives.
            disabled={!run.runId || run.stopping}
            className={cn(
              "flex items-center gap-1.5 rounded-chip border border-hairline px-2.5 py-1 text-xs",
              "transition-colors duration-1 ease-out hover:text-foreground disabled:opacity-50",
              focusRing,
            )}
          >
            <Square className="h-3 w-3" />
            {t("composer.stop")}
          </button>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Switch
          checked={pauseOnTaint}
          onChange={setPauseOnTaint}
          label={t("runs.pauseOnTaint")}
          disabled={running}
        />
        {/* aria-hidden: the switch already carries this exact text as its accessible name, and a
            screen reader would otherwise read the whole sentence twice. */}
        <span aria-hidden className="text-xs text-muted-foreground">
          {t("runs.pauseOnTaint")}
        </span>
      </div>
      <p className="text-xs text-muted-foreground">{t("runs.safetyNote")}</p>
      {/* What is about to judge this run, said BEFORE the first step. The server has always sent
          this frame; the client dropped it, so the sentence that mattered most — "nothing executable
          is judging this, a model will read the answer" — reached the user only afterwards, in the
          receipt, when the run was already over. A warning that arrives after the fact is a report. */}
      {run.verify ? (
        <p className={cn("text-xs", run.verify.command ? "text-muted-foreground" : "text-warn-foreground")}>
          {run.verify.command
            ? t("runs.judgedBy", { cmd: run.verify.command, src: run.verify.source })
            : t("runs.judgedByModel")}
        </p>
      ) : null}
      <RunStream lines={lines} />

      {/* The live pause first, then any parked from before this window existed — deduplicated, so a
          run that just paused is not also listed as an old one. */}
      {[
        ...(run.paused ? [run.paused] : []),
        ...(parked.data ?? []).filter((p) => p.thread_id !== run.paused?.thread_id),
      ].map((p) => (
        <PausedRunCard
          key={p.thread_id}
          run={p}
          onResolved={(threadId) => resume(threadId)}
        />
      ))}
    </div>
  );

  return variant === "panel" ? <Panel title={t("runs.new")}>{body}</Panel> : body;
}

/** The live progress of a run. Separate so Code can show it in its own column. */
export function RunStream({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <div
      // A run's own narration of what it is doing. `role="log"` so it accumulates rather than
      // interrupting; polite, because it is progress, not an alert.
      role="log"
      aria-live="polite"
      className="mt-1 space-y-1 rounded-chip bg-surface-2 p-2 font-mono text-xs text-muted-foreground"
    >
      {lines.map((line, i) => (
        <div key={i}>{line}</div>
      ))}
    </div>
  );
}

/** Turn one SSE event into a line a person can read. Exported so Runs and Code share the wording. */
export function liveLine(e: RunEvent, t: TFunc): string | null {
  if (e.kind === "status") return /planning/i.test(e.text) ? t("runs.planning") : e.text;
  if (e.kind === "attempt") return `${t("runs.attempt")} ${e.index} — ${t("runs.verifying")}`;
  if (e.kind === "result")
    return `${t("runs.attempt")} ${e.index}: ${e.success ? t("runs.passed") : t("runs.failed")}`;
  return null; // `final` is covered by onDone
}
