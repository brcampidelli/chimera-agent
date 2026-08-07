import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import hljs from "highlight.js";
import {
  Check,
  FileCode2,
  Folder,
  FolderGit2,
  Loader2,
  Pencil,
  Play,
  Save,
  Square,
  Undo2,
  X,
} from "lucide-react";
import {
  getFsFile,
  getGitStatus,
  getRuns,
  gitRevert,
  saveFile,
  type Approval,
  type Profile,
  type Reach,
  type RunEvent,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { Agents } from "@/components/Agents";
import { Conversation } from "@/components/code/Conversation";
import { PostureNote } from "@/components/code/PostureNote";
import { DiffView } from "@/components/code/DiffView";
import { SessionSidebar } from "@/components/code/SessionSidebar";
import { ProjectPicker } from "@/components/code/ProjectPicker";
import { useRunSession } from "@/lib/run-session";
import { PausedRunCard } from "@/components/run/PausedRunCard";
import { liveLine } from "@/components/run/RunLauncher";
import { useT, type TFunc } from "@/lib/i18n";
import { readWorkspace, writeWorkspace } from "@/lib/workspace";
import type { AttemptReceipt, FileDiff, RunReceipt } from "@/lib/types";

const fieldCls = "field w-full px-3 text-sm";

/** Map a filename extension to a highlight.js language id; unknown → let hljs auto-detect. */
const EXT_LANG: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  json: "json",
  md: "markdown",
  css: "css",
  scss: "scss",
  html: "xml",
  xml: "xml",
  yml: "yaml",
  yaml: "yaml",
  toml: "ini",
  ini: "ini",
  sh: "bash",
  bash: "bash",
  rs: "rust",
  go: "go",
  sql: "sql",
};

function highlightFile(content: string, name: string): string {
  try {
    const ext = name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
    const lang = EXT_LANG[ext];
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(content, { language: lang }).value;
    return hljs.highlightAuto(content).value;
  } catch {
    // Never let a highlighter error blank the viewer — show the raw (escaped) text instead.
    const div = document.createElement("div");
    div.textContent = content;
    return div.innerHTML;
  }
}

/** The center column: a syntax-highlighted viewer with an opt-in editor. Read-only is the default;
 *  "Edit" swaps in a mono textarea → Save PUTs it (atomic + newline-preserving + size-capped
 *  server-side). Truncated/binary files are NOT editable (saving would clobber the unseen remainder). */
function Viewer({ workspace, path }: { workspace: string; path: string | null }) {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["fs-file", workspace, path],
    queryFn: () => getFsFile(workspace || null, path as string),
    enabled: path !== null,
  });
  const name = path ? path.split("/").pop() ?? path : "";
  const loaded = q.data?.content ?? "";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);

  // Leave edit mode (and clear any flash) whenever the open file changes.
  useEffect(() => {
    setEditing(false);
    setSaveErr(false);
    setSavedFlash(false);
  }, [path]);

  const dirty = editing && draft !== loaded;
  // Only a clean, whole read is editable — a truncated (clipped at the read cap) or binary/non-text
  // file is not, since saving the shown text would overwrite the part we never loaded.
  const editable = path !== null && !!q.data && !q.data.note && !q.data.truncated;

  const html = useMemo(
    () => (q.data && q.data.content ? highlightFile(q.data.content, name) : ""),
    [q.data, name],
  );

  function startEdit() {
    setDraft(loaded);
    setSaveErr(false);
    setSavedFlash(false);
    setEditing(true);
  }
  function discard() {
    setDraft(loaded);
    setEditing(false);
    setSaveErr(false);
  }
  async function save() {
    if (!path || saving) return;
    setSaving(true);
    setSaveErr(false);
    try {
      await saveFile(workspace || null, path, draft);
      setEditing(false);
      setSavedFlash(true);
      // Re-read the file (its on-disk newline may differ from the draft) and refresh the tree.
      await qc.invalidateQueries({ queryKey: ["fs-file", workspace, path] });
      void qc.invalidateQueries({ queryKey: ["fs-tree"] });
    } catch {
      setSaveErr(true);
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col border-hairline lg:border-r">
      <div className="flex items-center gap-2 border-b border-hairline px-4 py-2.5">
        <FileCode2 className="h-4 w-4 shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
          {path ?? t("code.noFile")}
        </span>
        {dirty ? <Badge tone="warn">{t("code.dirty")}</Badge> : null}
        {q.data?.truncated ? <Badge tone="warn">{t("code.truncated")}</Badge> : null}
        {savedFlash && !editing ? (
          <span className="text-xs text-ok">{t("code.saved")}</span>
        ) : null}
        {editing ? (
          <>
            <Button size="sm" variant="ghost" disabled={saving} onClick={discard}>
              <X className="h-3.5 w-3.5" /> {t("code.discard")}
            </Button>
            <Button size="sm" disabled={saving || !dirty} onClick={() => void save()}>
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              {t("code.save")}
            </Button>
          </>
        ) : editable ? (
          <Button size="sm" variant="ghost" onClick={startEdit}>
            <Pencil className="h-3.5 w-3.5" /> {t("code.edit")}
          </Button>
        ) : null}
      </div>
      {editing ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <textarea
            className="min-h-0 flex-1 resize-none bg-transparent p-4 font-mono text-[12.5px] leading-relaxed text-foreground outline-none"
            value={draft}
            spellCheck={false}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="border-t border-hairline px-4 py-1.5 text-xs">
            {saveErr ? (
              <span className="text-bad">{t("code.saveError")}</span>
            ) : (
              <span className="text-muted-foreground">{t("code.noUndo")}</span>
            )}
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto">
          {path === null ? (
            <div className="px-4 py-6 text-sm text-muted-foreground">{t("code.viewerHint")}</div>
          ) : q.isLoading ? (
            <div className="flex justify-center py-10 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : q.isError ? (
            <div className="px-4 py-6 text-sm text-bad">{t("code.fileError")}</div>
          ) : q.data?.note ? (
            <div className="px-4 py-6 text-sm text-muted-foreground">{t("code.binaryNote")}</div>
          ) : (
            <pre className="overflow-x-auto p-4 text-[12.5px] leading-relaxed">
              <code className="hljs bg-transparent" dangerouslySetInnerHTML={{ __html: html }} />
            </pre>
          )}
        </div>
      )}
    </section>
  );
}

/** A hand-rolled unified-diff renderer: + green, - red, @@ dim headers, file headers muted. */
/** One attempt's diffs, honestly labeled: if the attempt was reverted, a banner says the changes
 *  were undone after verification failed (they are what it ATTEMPTED, not what is on disk). */
function AttemptDiffs({ attempt, t }: { attempt: AttemptReceipt; t: TFunc }) {
  // Render when there's something real to show: a diff, or the verifier's captured output.
  if (attempt.diffs.length === 0 && !attempt.verify_output) return null;
  return (
    <div className="space-y-2 px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground">
          {t("code.attempt")} {attempt.index}
        </span>
        {attempt.reverted ? <Badge tone="warn">↩ {t("runs.reverted")}</Badge> : null}
      </div>
      {attempt.reverted ? (
        <p className="text-xs text-warn-foreground">{t("code.revertedNote")}</p>
      ) : null}
      {attempt.diffs.map((diff: FileDiff) => (
        <details key={diff.path} className="rounded-chip bg-surface-2" open>
          <summary className="flex cursor-pointer items-center gap-2 px-2 py-1.5 font-mono text-xs text-foreground/80 hover:text-foreground">
            {diff.path}
            {diff.truncated ? <Badge tone="muted">{t("code.truncated")}</Badge> : null}
          </summary>
          <div className="px-2 pb-2">
            <DiffView patch={diff.patch} />
          </div>
        </details>
      ))}
      {/* The verifier's REAL captured stdout/stderr for this attempt (the concrete test/assert
          output). Collapsed by default; shown only when non-empty — never fabricated. */}
      {attempt.verify_output ? (
        <details className="rounded-chip bg-surface-2">
          <summary className="cursor-pointer px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground">
            {t("code.verifyOutput")}
          </summary>
          <pre className="overflow-x-auto rounded-chip bg-surface-2 px-2 py-2 font-mono text-xs leading-relaxed text-muted-foreground">
            <code className="whitespace-pre-wrap break-all">{attempt.verify_output}</code>
          </pre>
        </details>
      ) : null}
    </div>
  );
}

/** The live play-by-play: one collapsible real unified diff per file the agent edited AS it happens.
 *  Streamed from the run's `edit` events (read off disk before/after each write — never fabricated).
 *  Order is the true edit sequence; a path can recur if a later step overwrites an earlier edit. The
 *  receipt's per-file diffs below stay the authoritative final view — this is the step-by-step. */
function LiveEdits({ edits, t }: { edits: { path: string; patch: string }[]; t: TFunc }) {
  if (edits.length === 0) return null;
  return (
    <div className="space-y-2 border-b border-hairline px-4 py-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {t("code.liveEdits")}
      </div>
      {edits.map((e, i) => (
        <details key={i} className="rounded-chip bg-surface-2" open>
          <summary className="flex cursor-pointer items-center gap-2 px-2 py-1.5 font-mono text-xs text-foreground/80 hover:text-foreground">
            <span className="shrink-0 text-muted-foreground">{i + 1}.</span>
            <span className="truncate">{e.path}</span>
          </summary>
          <div className="px-2 pb-2">
            <DiffView patch={e.patch} />
          </div>
        </details>
      ))}
    </div>
  );
}


/** Parse plan text into display steps (strip a leading "N." / "N)"), mirroring the backend parser.
 *  Used only to render the approved plan as a numbered checklist — the backend re-parses the raw text. */
function planStepsOf(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.replace(/^\s*\d+[.)]\s*/, "").trim())
    .filter(Boolean);
}


/** The right/bottom column: instruct + verify-or-revert run, then the newest run's real diffs. */
function RunPanel({
  workspace,
  onRan,
  handOff,
  onBusy,
  posture,
  profile,
}: {
  workspace: string;
  onRan: () => void;
  /** Which tier each role draws from — the same profile the conversation uses. */
  profile: Profile;
  /** The same reach/approval the conversation uses — the two buttons must not differ in what the
   *  agent is allowed to do, only in whether the change gets verified. */
  posture: { reach: Reach; approval: Approval };
  /** Text handed over from the conversation's "Run with verification". A new object each time, so
   *  pressing the button twice with the same words still lands — a bare string would compare equal
   *  and the second press would do nothing, which reads as the button being broken. */
  handOff?: { text: string; at: number } | null;
  /** Whether a run is in flight, lifted so the conversation can refuse to edit the same workspace
   *  underneath it. */
  onBusy?: (running: boolean) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [task, setTask] = useState("");
  const [verify, setVerify] = useState("");
  // The steps a RUNNING run is following. Kept while the preview card goes, because it is evidence
  // of what the agent is doing rather than a control asking what it should do — which is the whole
  // distinction this screen now turns on.
  const [runPlan, setRunPlan] = useState<string[] | null>(null);
  // The run's lifecycle comes from the SHARED session, not from private state here.
  //
  // This panel used to keep its own `running`/`runId`/`stopping`/`lines` and call `streamRun`
  // directly — with `onDone` and `onError` and no `onPaused`. The dispatcher does
  // `h.onPaused?.(…)`, so with the default approval ("suspicious" → pause_on_taint → a thread is
  // minted) a run that consumed untrusted content emitted `paused` INSTEAD of `done`, the frame fell
  // on the floor, and this panel said "running" forever while the work sat waiting for a verdict
  // nobody could see or give. `useRunSession` already handles pause, renders the card, and survives
  // navigation; `RunLauncher` was extracted to end three copies of this state and had acquired one
  // consumer. This is the adoption that never happened.
  const session = useRunSession();
  const { running, stopping } = session;
  // Derived at RENDER time from the raw events, so a language change cannot leave half a transcript
  // frozen in the previous locale — the reason the session stores events rather than strings.
  const lines = useMemo(() => {
    const out = session.events
      .map((e: RunEvent) => liveLine(e, t))
      .filter((l): l is string => !!l);
    // The ending, derived like the rest. A cooperative Stop ends the run with stopped_reason
    // "cancelled" — said honestly rather than reading as a plain failure.
    if (session.done) {
      out.push(
        session.done.stopped_reason === "cancelled"
          ? t("code.doneCancelled")
          : session.done.success
            ? t("code.doneOk")
            : t("code.doneFail"),
      );
    } else if (session.broken) {
      // Our view of the run broke, which is NOT the run failing — but the user is owed an ending
      // either way, and silence would read as "still going".
      out.push(t("code.doneFail"));
    }
    return out;
  }, [session.events, session.done, session.broken, t]);
  const liveEdits = useMemo(
    () =>
      session.events
        .filter((e: RunEvent) => e.kind === "edit" && e.path && e.patch)
        .map((e: RunEvent) => ({ path: e.path as string, patch: e.patch as string })),
    [session.events],
  );
  const [receipt, setReceipt] = useState<RunReceipt | null>(null);
  const [reverting, setReverting] = useState(false);
  // Shown after a git-backed discard. A flag rather than a line pushed into the transcript, because
  // the transcript is now derived from the run's own events and must keep saying what the RUN did.
  const [reverted, setReverted] = useState(false);
  const [revertErr, setRevertErr] = useState(false);
  // Whether the workspace is a git repo — gates the git-backed "Discard changes" control. Shares the
  // ["git-status", workspace] cache with the GitPanel, so a commit/revert there refreshes this too.
  const gitQ = useQuery({
    queryKey: ["git-status", workspace],
    queryFn: () => getGitStatus(workspace || null),
  });
  const isRepo = !!gitQ.data?.is_repo;

  // A hand-off from the conversation fills the task field rather than starting the run outright.
  // "Run with verification" is where the user chooses a verify command and how many attempts to
  // allow — starting immediately would silently make those choices for them.
  useEffect(() => {
    if (handOff?.text) setTask(handOff.text);
  }, [handOff]);

  useEffect(() => {
    onBusy?.(running);
  }, [running, onBusy]);

  // The run's changed paths, de-duplicated across attempts — what a git-backed discard is scoped to.
  const changedPaths = useMemo(() => {
    if (!receipt) return [];
    const set = new Set<string>();
    for (const a of receipt.attempts) for (const d of a.diffs) set.add(d.path);
    return [...set];
  }, [receipt]);

  // Accept: a successful run's changes are already on disk — just clear the pending-review UI.
  function accept() {
    // Only the pending-review UI is cleared. The transcript and the diffs are derived from the
    // session's events and belong to the run that produced them — blanking them here used to erase
    // the record of what was just accepted.
    setReceipt(null);
    setRevertErr(false);
  }

  // Discard: git-backed revert scoped to THIS run's paths (never workspace-wide). Enabled only in a
  // git repo; it reverts the git-visible changes, not files git ignores/can't track.
  async function discard() {
    if (!isRepo || reverting || changedPaths.length === 0) return;
    setReverting(true);
    setRevertErr(false);
    try {
      const res = await gitRevert(workspace || null, changedPaths);
      if (!res.ok) {
        setRevertErr(true);
        return;
      }
      setReceipt(null);
      setReverted(true);
      void qc.invalidateQueries({ queryKey: ["fs-tree"] });
      void qc.invalidateQueries({ queryKey: ["fs-file"] });
      void qc.invalidateQueries({ queryKey: ["git-status"] });
      onRan();
    } catch {
      setRevertErr(true);
    } finally {
      setReverting(false);
    }
  }

  // Preview the plan: a pure planner call (NO edits, NO tools) so the steps can be reviewed/edited

  // `plan`: when a non-empty string is passed, the run follows THIS approved plan verbatim (no
  // re-planning). `null`/undefined = the run plans for itself, as before.
  // Cooperative Stop: ask the backend to halt this run BEFORE its next attempt. An in-flight model
  // step can't be interrupted, so this never kills instantly — it stops after the current attempt
  // finishes. `stopping` stays set (disabling re-click) until the run's own `done` clears the state.
  // Cooperative Stop: the session owns the cancel handle and the "stopping until the terminal frame"
  // rule, so there is one implementation of it rather than three that drift.
  const stop = session.stop;

  function start(plan?: string | null) {
    if (!task.trim() || running) return;
    setReceipt(null);
    setReverted(false);
    // Only surface a task list when we injected a real plan — never fabricate steps for a plain run.
    setRunPlan(plan && plan.trim() ? planStepsOf(plan) : null);

    session.start(runRequest(plan), { onDone: afterRun });
  }

  /** The request this panel sends, in one place so a resume cannot drift from a first start. */
  function runRequest(plan?: string | null, threadId?: string) {
    return {
        task: task.trim(),
        verify: verify.trim() || null,
        workspace: workspace || null,
        max_attempts: 3,
        plan: plan && plan.trim() ? plan : null,
        model: null,
        posture,
        profile,
        profile_source: "system",
        // Carried on a resume so the backend continues THAT run rather than starting a new one.
        // Kept armed rather than cleared: an "edit"/"accept" resume finalises without re-running, but
        // a "respond" makes a fresh attempt that can read untrusted content again and must be able
        // to stop and ask a second time.
        thread_id: threadId ?? null,
    };
  }

  // Reached for a finished run AND for a transport failure, but NOT for a pause — a paused run has
  // not reached a verdict, so fetching its receipt would show the PREVIOUS run's as if it were this
  // one's. The card the session renders is what the user acts on instead.
  function afterRun() {
          void (async () => {
            // The stream's `done` payload has no diffs — the newest receipt carries the real ones.
            try {
              const runs = await getRuns();
              setReceipt(runs[0] ?? null);
            } catch {
              setReceipt(null);
            }
            // The workspace may have changed (or been reverted) — refresh tree, file and git status.
            void qc.invalidateQueries({ queryKey: ["fs-tree"] });
            void qc.invalidateQueries({ queryKey: ["fs-file"] });
            void qc.invalidateQueries({ queryKey: ["git-status"] });
            onRan();
          })();
  }

  /** Continue a run that stopped for a verdict, under its own thread id. */
  function resume(threadId: string) {
    session.clearPaused();
    session.start(runRequest(null, threadId), { onDone: afterRun });
  }

  return (
    <section className="flex min-h-0 flex-col overflow-auto border-t border-hairline">
      <div className="space-y-2.5 border-b border-hairline p-3">
        {/* No heading here: the <details> summary that reveals this panel already carries the same
            words, and printing them twice reads as two sections when it is one. */}
        <textarea
          className={`${fieldCls} min-h-[72px] resize-y py-2`}
          placeholder={t("code.taskPlaceholder")}
          value={task}
          onChange={(e) => setTask(e.target.value)}
          disabled={running}
        />
        <input
          className={`${fieldCls} h-9 font-mono text-xs`}
          placeholder={t("code.verifyPlaceholder")}
          value={verify}
          onChange={(e) => setVerify(e.target.value)}
          disabled={running}
        />
        {/* No model field, no single/fusion/cascade toggle, no attempt budget, no plan preview.
            Each asked the user to configure a run before starting one, and each now has a
            server-side answer the receipt records — a default the system applied, never a decision
            attributed to somebody who was not asked. The plan preview goes for a different reason:
            approving a plan before any file is touched is a second confirmation step for a run that
            already reverts itself when the verifier says no. */}
        <div className="flex flex-wrap items-center gap-3">
          <Button size="sm" disabled={!task.trim() || running} onClick={() => start()}>
            {running ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> {t("code.running")}
              </>
            ) : (
              <>
                <Play className="h-4 w-4" /> {t("code.run")}
              </>
            )}
          </Button>
          {running ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={!session.runId || stopping}
              onClick={() => void stop()}
              title={t("code.stopTooltip")}
            >
              {stopping ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> {t("code.stopping")}
                </>
              ) : (
                <>
                  <Square className="h-4 w-4" /> {t("code.stop")}
                </>
              )}
            </Button>
          ) : null}
        </div>
        {running && runPlan && runPlan.length > 0 ? (
          <div className="space-y-1 rounded-chip border border-border bg-surface-2 p-2.5">
            <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {t("code.planForRun")}
            </div>
            <ol className="space-y-1 pl-1">
              {runPlan.map((step, i) => (
                <li key={i} className="flex gap-2 text-xs text-foreground/80">
                  <span className="shrink-0 font-mono text-muted-foreground">{i + 1}.</span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
        {lines.length > 0 ? (
          <div className="space-y-1 rounded-chip bg-surface-2 p-2 font-mono text-xs text-muted-foreground">
            {lines.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
            {/* Appended, not substituted for the transcript: what the run did and what you then did
                about it are two facts, and overwriting the first with the second used to erase the
                record of the work being discarded. */}
            {reverted ? <div className="text-warn">{t("code.git.reverted")}</div> : null}
          </div>
        ) : null}
        {/* The run stopped for a verdict. This is the frame that used to fall on the floor: the
            panel passed no `onPaused`, the dispatcher's optional chaining swallowed it, and the run
            sat here reading "running" forever. */}
        {session.paused ? (
          <PausedRunCard run={session.paused} onResolved={(threadId) => resume(threadId)} />
        ) : null}
      </div>

      <div className="min-h-0 flex-1">
        <LiveEdits edits={liveEdits} t={t} />
        {receipt ? (
          <>
            <div className="flex flex-wrap items-center gap-2 border-b border-hairline px-4 py-2">
              <Button size="sm" variant="ghost" onClick={accept}>
                <Check className="h-3.5 w-3.5" /> {t("code.git.accept")}
              </Button>
              <span title={isRepo ? undefined : t("code.git.discardNeedsGit")}>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={!isRepo || reverting || changedPaths.length === 0}
                  onClick={() => void discard()}
                >
                  {reverting ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Undo2 className="h-3.5 w-3.5" />
                  )}
                  {t("code.git.discardRun")}
                </Button>
              </span>
              {revertErr ? (
                <span className="text-xs text-bad">{t("code.git.revertError")}</span>
              ) : !isRepo ? (
                <span className="text-xs text-muted-foreground">{t("code.git.discardNeedsGit")}</span>
              ) : null}
            </div>
            {receipt.attempts.some((a) => a.diffs.length > 0 || a.verify_output) ? (
              <div className="divide-y divide-hairline">
                <div className="px-4 pt-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {t("code.diff")}
                </div>
                {receipt.attempts.map((a) => (
                  <AttemptDiffs key={a.index} attempt={a} t={t} />
                ))}
              </div>
            ) : (
              <div className="px-4 py-4 text-xs text-muted-foreground">{t("code.noDiff")}</div>
            )}
          </>
        ) : null}
      </div>
    </section>
  );
}

export function Code() {
  const t = useT();
  const qc = useQueryClient();
  // Lazy initialiser, not `useState(readWorkspace())`: the latter reads storage on every render.
  const [workspace, setWorkspace] = useState(readWorkspace);
  const [openFile, setOpenFile] = useState<string | null>(null);
  const [projectDraft, setProjectDraft] = useState(readWorkspace);
  // Which stored conversation is on screen, and a key that remounts the transcript when it
  // changes — the conversation holds its exchanges in state, so switching sessions has to
  // discard them rather than let the previous project's turns sit above the new one.
  const [picking, setPicking] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [conversationKey, setConversationKey] = useState(0);
  // The conversation and the run share one workspace, so they share two facts: what the user asked
  // (handed over by "Run with verification") and whether a run is already in flight.
  const [handOff, setHandOff] = useState<{ text: string; at: number } | null>(null);
  // A confirmed decomposition. Keyed by `at` so a second batch is a new board rather than a restart
  // of the old one — the board starts its runs on mount, which is only correct if mounting is what a
  // new batch does.
  const [batch, setBatch] = useState<{ tasks: string[]; at: number } | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  // Fixed, not chosen: edit the workspace, no shell, stop and ask if the run read something
  // untrusted. These are still SENT on every request — omitting them resolves to no tool denials
  // and no pause whatsoever, which is more permissive than any corner a user could have selected.
  // What changed is that nobody is asked to configure them before typing; what the agent may do is
  // stated in the transcript instead, which is where a capability belongs once it is not a control.
  const reach: Reach = "workspace";
  const approval: Approval = "suspicious";
  const posture = useMemo(() => ({ reach, approval }), [reach, approval]);
  // Same: a default the system applies, and the receipt records `profile_source: "system"` so the
  // cost panel never counts it beside a profile somebody deliberately picked.
  const profile: Profile = "balanced";

  const refreshOpenFile = useCallback(() => {
    if (openFile) void qc.invalidateQueries({ queryKey: ["fs-file", workspace, openFile] });
  }, [qc, workspace, openFile]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 border-b border-hairline px-5 py-3 text-accent">
        <FileCode2 className="h-5 w-5" />
        <h1 className="text-sm font-semibold text-foreground">{t("code.title")}</h1>
      </div>
      {/* Which project, on one line, the way a coding tool names the repo it is pointed at. What
          used to be here was a file TREE: a quarter of the window asking the user to navigate to
          the file they wanted changed. That is the job the agent is for — it greps, it reads, it
          finds. A browser in front of an agent that can search is the user doing the agent's work,
          and it also framed the screen as "maintain THIS folder" rather than "get MY work done".
          Files still open — from the transcript, by clicking a path the agent actually touched. */}
      <form
        className="flex items-center gap-2 border-b border-hairline px-5 py-2"
        onSubmit={(e) => {
          e.preventDefault();
          const next = projectDraft.trim();
          setWorkspace(next);
          writeWorkspace(next);
          setOpenFile(null);
          void qc.invalidateQueries({ queryKey: ["fs-file"] });
          void qc.invalidateQueries({ queryKey: ["git-status"] });
        }}
      >
        <FolderGit2 className="h-4 w-4 shrink-0 text-accent" />
        <input
          className={`${fieldCls} h-7 max-w-xl font-mono text-xs`}
          placeholder={t("code.workspacePlaceholder")}
          value={projectDraft}
          onChange={(e) => setProjectDraft(e.target.value)}
        />
        <Button size="sm" type="submit" variant="ghost">
          {t("code.open")}
        </Button>
        {/* The typed field stays: someone who knows the path should not have to click through to
            it, and it is what the tests and a paste from a terminal both use. The picker is for
            everyone else. */}
        <Button size="sm" variant="ghost" onClick={() => setPicking((p) => !p)}>
          <Folder className="h-4 w-4" /> {t("code.picker.browse")}
        </Button>
      </form>
      {picking ? (
        <div className="border-b border-hairline px-5 py-2">
          <ProjectPicker
            onCancel={() => setPicking(false)}
            onPick={(path) => {
              setPicking(false);
              setWorkspace(path);
              writeWorkspace(path);
              setProjectDraft(path);
              setOpenFile(null);
              void qc.invalidateQueries({ queryKey: ["git-status"] });
            }}
          />
        </div>
      ) : null}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <SessionSidebar
          workspace={workspace}
          activeSession={sessionId}
          onNew={() => {
            // A new conversation, not a cleared one: the old transcript stays on disk and stays in
            // the list. Clearing used to be the only way to start over, and it deleted the session.
            setSessionId(null);
            setConversationKey((n) => n + 1);
            setOpenFile(null);
          }}
          onResume={(session) => {
            setSessionId(session.id);
            setConversationKey((n) => n + 1);
            setOpenFile(null);
            if (session.workspace !== workspace) {
              setWorkspace(session.workspace);
              writeWorkspace(session.workspace);
              setProjectDraft(session.workspace);
            }
          }}
          onProject={(next) => {
            setWorkspace(next);
            writeWorkspace(next);
            setProjectDraft(next);
            setOpenFile(null);
          }}
        />
        {/* The conversation IS the screen. It used to be one of five panels in a 384px column, and
            the arithmetic did not work: the panels that could not shrink took every pixel and this
            was laid out at zero height. Git and the cost table now live on Work, where reviewing
            what a run did belongs; the two settings became one row inside the composer. */}
        <main className="flex min-h-0 flex-1 flex-col">
          <Conversation
            key={conversationKey}
            resumeSession={sessionId}
            workspace={workspace}
            openFile={openFile}
            onOpenFile={setOpenFile}
            onHandOff={(text) => setHandOff({ text, at: Date.now() })}
            onBatch={(tasks) => setBatch({ tasks, at: Date.now() })}
            onEdited={refreshOpenFile}
            busyElsewhere={runBusy}
            posture={posture}
            profile={profile}
            /* No selectors. What they expressed is now a server default the app SENDS (never omits
               — an absent posture means no tool denials and no pause at all, which is more permissive
               than any corner of the grid a user could have chosen) and a line of evidence in the
               transcript once it becomes relevant. */
            controls={<PostureNote workspace={workspace} reach={reach} approval={approval} />}
          />
          {/* Folded away until it is doing something. The autonomous launcher is 389px of controls —
              attempts, plan preview, browser verification — and it was sitting under the
              conversation permanently, taking half the column from the thing people actually type
              in. It stays MOUNTED (details hides, it does not unmount) because the conversation's
              "run with verification" button hands text to it, and it opens itself when a run is in
              flight so a running agent is never something you have to go looking for. */}
          <details
            open={runBusy || handOff !== null}
            className="shrink-0 border-t border-hairline"
          >
            <summary className="cursor-pointer px-4 py-2 text-sm text-muted-foreground hover:text-foreground">
              {t("code.instruction")}
            </summary>
            {/* Re-read the currently open file after the agent edited or reverted the workspace. */}
            <RunPanel
              workspace={workspace}
              onRan={refreshOpenFile}
              handOff={handOff}
              onBusy={setRunBusy}
              posture={posture}
              profile={profile}
            />
          </details>
          {/* Several agents at once, once someone said yes to running several agents at once. This
              was a destination — a tab you picked before knowing whether the work was parallel, with
              its own launcher asking for tasks, a model, a worker count and a fusion mode. It is a
              consequence now, and the only question it still asks was asked before the worktrees
              existed rather than reported after they did. */}
          {batch ? (
            <div className="min-h-0 shrink-0 border-t border-hairline">
              <Agents
                key={batch.at}
                workspace={workspace}
                tasks={batch.tasks}
                posture={posture}
                profile={profile}
              />
            </div>
          ) : null}
        </main>
        {/* The viewer is a consequence of opening a file, not a permanent third of the window. */}
        {openFile ? (
          <div className="flex min-h-0 flex-col border-hairline lg:w-[28rem] lg:border-l">
            <Viewer workspace={workspace} path={openFile} />
          </div>
        ) : null}
      </div>
    </div>
  );
}
