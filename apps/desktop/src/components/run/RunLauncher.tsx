import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ListChecks, Loader2, Play, Square, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { PausedRunCard } from "@/components/run/PausedRunCard";
import { focusRing } from "@/components/ui/focus";
import { NO_OVERRIDE, RolesBar, toRoleModels, type RoleOverride } from "@/components/code/RolesBar";
import { getRequirements, type TaskRequirement, getPlan, getPausedRuns, type Profile, type RunEvent } from "@/lib/api";
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
 * Now there is one, and it no longer owns the task or the check either: those are the console's,
 * shared with the three other ways the same task can be run. What is left here is what only a run
 * means — how many attempts, who writes the patch, whether it stops when it reads something
 * untrusted.
 *
 * The form is local; the RUN is not. It lives in the shell's run session, so leaving this screen
 * no longer abandons it: come back and the progress is still here, and the Stop below is the same
 * Stop the status bar offers from every other screen.
 */
export function RunLauncher({
  task,
  verify,
  workspace,
  onBusy,
}: {
  /** The shared task and check, from the console above. */
  task: string;
  verify: string;
  /** Where the run works. The console shows it; this screen no longer asks for it, because a
   *  second folder field is a second answer to one question — and the field it used to show
   *  offered "defaults to the app's workspace", which for an installed build is the install
   *  directory and nobody's project. */
  workspace: string;
  onBusy?: (busy: boolean) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const run = useRunSession();
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
  // The requirement checklist, alongside the plan and read at the same moment. `null` means nobody
  // has been asked and the run carries no checklist at all; `[]` means somebody read the list and
  // deleted every line, which is a different statement and is sent as such.
  const [reqs, setReqs] = useState<TaskRequirement[] | null>(null);
  const [reqNote, setReqNote] = useState("");
  // Three seams the CLI has always had and no screen sent. One control per idea rather than one
  // per flag: `repo_map` and `explorer` are two halves of "help it find its way around a project
  // that already exists", and nobody choosing between them would be choosing anything.
  const [knowsRepo, setKnowsRepo] = useState(false);
  const [genTests, setGenTests] = useState(false);
  const [replan, setReplan] = useState(false);
  const [requireDiff, setRequireDiff] = useState(false);
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

  // The shell's run session is global, so this is true for a run started from the Code screen's
  // hand-off too — and correctly so: the stream and the Stop below are that run's, on this screen.
  useEffect(() => {
    onBusy?.(running);
  }, [running, onBusy]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["runs"] });
  };

  /** The run request for a thread, so start and resume cannot drift apart. */
  const request = (threadId: string | null) => ({
    task: task.trim(),
    verify: verify.trim() || null,
    workspace: workspace || null,
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
    // The checklist as the person left it — not re-derived. Deleting a line here has to be a line
    // the run is not graded against, or the review is decoration; and a list nobody was shown must
    // never become an acceptance gate, which is why `null` travels as `null`.
    requirements: reqs,
    repo_map: knowsRepo,
    explorer: knowsRepo,
    replan,
    require_diff: requireDiff,
    // Only where it replaces something weaker. With a verify command the tests already are the
    // ground truth, and with no reviewed checklist there is nothing to ground generation in — the
    // loop checks both itself, so sending it anyway is a no-op rather than a conflict, and the
    // screen simply does not offer it where it would be one.
    gen_tests: genTests && !verify.trim() && (reqs?.length ?? 0) > 0,
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
    setReqNote("");
    try {
      // Both texts, together and in parallel: they are read in one sitting, and it is holding the
      // plan against the checklist that shows up what was never asked for. Two calls rather than
      // one route doing both, so each still does one thing and a caller that wants only the plan
      // pays for only the plan.
      // Settled, not all: one failing must not take the other down. `Promise.all` rejects on the
      // first rejection, so a checklist call that fell over discarded a perfectly good plan and
      // reported the PLAN as failed — a broken half reporting the working half as broken.
      const [p, r] = await Promise.allSettled([
        getPlan(task.trim(), workspace || null),
        getRequirements(task.trim()),
      ]);
      // Neither half is trusted to have the shape its type promises. `allSettled` reports a
      // resolved-with-nothing call as `fulfilled`, so reading through `.value` without checking
      // throws INSIDE the handler — past the point where a rejection could be caught — and leaves
      // the panel open, empty and silent. Caught by CI on a suite whose mock resolves to
      // undefined, which is exactly what a stubbed or half-upgraded backend looks like.
      const plan = p.status === "fulfilled" ? p.value : null;
      if (plan && typeof plan.text === "string") {
        setPlan(plan.text);
        // The endpoint degrades to an empty plan with a note rather than failing, so an empty
        // answer needs a sentence: a blank box would read as "it plans to do nothing".
        if (!plan.text) setPlanNote(plan.note || t("runs.plan.empty"));
      } else {
        setPlanNote(t("runs.plan.failed"));
      }
      const got = r.status === "fulfilled" ? r.value : null;
      if (got && Array.isArray(got.items)) {
        setReqs(got.items);
        if (!got.items.length) setReqNote(got.note || t("runs.reqs.empty"));
      } else {
        setReqNote(t("runs.reqs.empty"));
      }
    } catch {
      // Still here, and it has to be: `allSettled` removes the rejection but not every way this
      // block can throw, and a preview that dies silently leaves a panel that never closes.
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

  return (
    <div className="space-y-2.5">
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
                // Both, together. A run that carried an approved checklist alongside a discarded
                // plan would be carrying half a review — and the half nobody meant to keep.
                setReqs(null);
                setReqNote("");
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

          {/* The checklist, under the plan and read in the same sitting. Holding one against the
              other is what surfaces the thing nobody asked for: it is reading "include: a contact
              form" that reminds somebody they never said "with the menu". */}
          {reqs !== null || reqNote ? (
            <div className="space-y-1 border-t border-hairline pt-2">
              <p className="text-xs font-medium">{t("runs.reqs.title")}</p>
              {reqNote ? <p className="text-xs text-warn-foreground">{reqNote}</p> : null}
              <ul className="space-y-1">
                {(reqs ?? []).map((r, i) => (
                  // Keyed by position, not by content. Keying on the text destroys and rebuilds
                  // the input on every keystroke: focus is lost after the first character and the
                  // rest of what somebody types goes nowhere — on the one panel whose entire value
                  // is being editable. Position IS the identity of a line in an editable list.
                  <li key={i} className="flex items-start gap-1.5">
                    {/* The kind, labelled. A weak model drops `avoid` and `include` first — "must
                        do X" survives context growth and "don't do Y" quietly does not — so which
                        kind a line is happens to be the most useful thing on it. */}
                    <span className="mt-0.5 shrink-0 rounded-chip bg-surface-2 px-1 text-xs uppercase tracking-wide text-muted-foreground">
                      {t(`runs.reqs.kind.${r.kind === "avoid" || r.kind === "include" ? r.kind : "do"}`)}
                    </span>
                    <input
                      className={cn(fieldCls, "h-6 flex-1 px-1.5 text-xs")}
                      aria-label={t("runs.reqs.line", { n: i + 1 })}
                      value={r.text}
                      onChange={(e) =>
                        setReqs((prev) =>
                          (prev ?? []).map((x, j) => (j === i ? { ...x, text: e.target.value } : x)),
                        )
                      }
                      disabled={running}
                    />
                    <button
                      type="button"
                      aria-label={t("runs.reqs.drop", { text: r.text })}
                      className={cn("mt-0.5 px-1 text-muted-foreground hover:text-bad", focusRing)}
                      onClick={() => setReqs((prev) => (prev ?? []).filter((_, j) => j !== i))}
                      disabled={running}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
              <button
                type="button"
                className={cn("text-xs text-muted-foreground hover:text-foreground", focusRing)}
                onClick={() => setReqs((prev) => [...(prev ?? []), { text: "", kind: "include" }])}
                disabled={running}
              >
                + {t("runs.reqs.add")}
              </button>
              <p className="text-xs text-muted-foreground">{t("runs.reqs.hint")}</p>
              {/* Offered only where it changes the verdict. With a test command already typed the
                  tests ARE the ground truth; with no lines left there is nothing to ground the
                  generation in. A control that does nothing teaches people not to read the ones
                  that do. */}
              {!verify.trim() && (reqs?.length ?? 0) > 0 ? (
                <label className="flex items-start gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={genTests}
                    onChange={(e) => setGenTests(e.target.checked)}
                    disabled={running}
                  />
                  <span>{t("runs.seams.genTests")}</span>
                </label>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
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
      {/* `flex-wrap`: this row grew two checkboxes and a second button, and without wrapping the
          last item is squeezed until its label breaks. Reflowing is the behaviour that survives the
          next control somebody adds — and the next translation, which may be longer than this one. */}
      <div className="flex flex-wrap items-center gap-3">
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
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={knowsRepo}
            onChange={(e) => setKnowsRepo(e.target.checked)}
            disabled={running}
          />
          {t("runs.seams.knowsRepo")}
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={replan}
            onChange={(e) => setReplan(e.target.checked)}
            disabled={running}
          />
          {t("runs.seams.replan")}
        </label>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={requireDiff}
            onChange={(e) => setRequireDiff(e.target.checked)}
            disabled={running}
          />
          {t("runs.seams.requireDiff")}
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
