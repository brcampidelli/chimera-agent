import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import hljs from "highlight.js";
import {
  Camera,
  Check,
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  FileCode2,
  Folder,
  FolderOpen,
  GitBranch,
  ListChecks,
  Loader2,
  Pencil,
  Play,
  Save,
  Square,
  Terminal,
  Undo2,
  X,
} from "lucide-react";
import {
  cancelRun,
  captureScreenshot,
  getFsFile,
  getFsTree,
  getGitDiff,
  getGitStatus,
  getPlan,
  getRuns,
  gitCommit,
  gitRevert,
  saveFile,
  streamExec,
  streamRun,
  type RunEvent,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useT, type TFunc } from "@/lib/i18n";
import type { AttemptReceipt, FileDiff, FsNode, GitFile, RunReceipt } from "@/lib/types";
import { cn } from "@/lib/utils";

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

/** One lazy tree node. A directory fetches its children (one level) only when expanded. */
function TreeNode({
  workspace,
  node,
  depth,
  activePath,
  onOpen,
}: {
  workspace: string;
  node: FsNode;
  depth: number;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["fs-tree", workspace, node.path],
    queryFn: () => getFsTree(workspace || null, node.path),
    enabled: node.is_dir && open,
  });
  const pad = { paddingLeft: `${depth * 12 + 8}px` };

  if (!node.is_dir) {
    const active = activePath === node.path;
    return (
      <button
        onClick={() => onOpen(node.path)}
        style={pad}
        title={node.path}
        className={cn(
          "flex w-full items-center gap-1.5 py-1 pr-2 text-left text-xs transition-colors",
          active ? "bg-accent/15 text-accent" : "text-muted-foreground hover:text-foreground",
        )}
      >
        <FileIcon className="h-3.5 w-3.5 shrink-0 opacity-70" />
        <span className="truncate">{node.name}</span>
      </button>
    );
  }

  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        style={pad}
        title={node.path}
        className="flex w-full items-center gap-1 py-1 pr-2 text-left text-xs text-foreground/80 hover:text-foreground"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0" />
        )}
        {open ? (
          <FolderOpen className="h-3.5 w-3.5 shrink-0 text-accent/80" />
        ) : (
          <Folder className="h-3.5 w-3.5 shrink-0 text-accent/80" />
        )}
        <span className="truncate">{node.name}</span>
      </button>
      {open ? (
        q.isLoading ? (
          <div style={{ paddingLeft: `${depth * 12 + 24}px` }} className="py-1 text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          </div>
        ) : q.isError ? (
          <div style={{ paddingLeft: `${depth * 12 + 24}px` }} className="py-1 text-[11px] text-bad">
            {t("code.treeError")}
          </div>
        ) : (
          (q.data?.entries ?? []).map((child) => (
            <TreeNode
              key={child.path}
              workspace={workspace}
              node={child}
              depth={depth + 1}
              activePath={activePath}
              onOpen={onOpen}
            />
          ))
        )
      ) : null}
    </div>
  );
}

/** The left column: a workspace path input and a lazy file tree rooted at it. */
function TreePanel({
  workspace,
  onWorkspace,
  activePath,
  onOpen,
}: {
  workspace: string;
  onWorkspace: (ws: string) => void;
  activePath: string | null;
  onOpen: (path: string) => void;
}) {
  const t = useT();
  const [input, setInput] = useState(workspace);
  const rootQ = useQuery({
    queryKey: ["fs-tree", workspace, ""],
    queryFn: () => getFsTree(workspace || null, ""),
  });

  return (
    <aside className="flex min-h-0 flex-col border-white/5 lg:w-72 lg:border-r">
      <div className="border-b border-white/5 p-3">
        <label className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t("code.workspace")}
        </label>
        <form
          className="mt-1.5 flex gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            onWorkspace(input.trim());
          }}
        >
          <input
            className={`${fieldCls} h-8 font-mono text-xs`}
            placeholder={t("code.workspacePlaceholder")}
            value={input}
            onChange={(e) => setInput(e.target.value)}
          />
          <Button size="sm" type="submit" variant="ghost">
            {t("code.open")}
          </Button>
        </form>
      </div>
      <div className="min-h-0 flex-1 overflow-auto py-1">
        {rootQ.isLoading ? (
          <div className="flex justify-center py-6 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : rootQ.isError ? (
          <div className="px-3 py-4 text-xs text-bad">{t("code.treeError")}</div>
        ) : (rootQ.data?.entries.length ?? 0) === 0 ? (
          <div className="px-3 py-4 text-xs text-muted-foreground">{t("code.treeEmpty")}</div>
        ) : (
          rootQ.data?.entries.map((node) => (
            <TreeNode
              key={node.path}
              workspace={workspace}
              node={node}
              depth={0}
              activePath={activePath}
              onOpen={onOpen}
            />
          ))
        )}
        {rootQ.data?.capped ? (
          <div className="px-3 py-2 text-[11px] text-muted-foreground">{t("code.treeCapped")}</div>
        ) : null}
      </div>
    </aside>
  );
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
    <section className="flex min-h-0 flex-1 flex-col border-white/5 lg:border-r">
      <div className="flex items-center gap-2 border-b border-white/5 px-4 py-2.5">
        <FileCode2 className="h-4 w-4 shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
          {path ?? t("code.noFile")}
        </span>
        {dirty ? <Badge tone="warn">{t("code.dirty")}</Badge> : null}
        {q.data?.truncated ? <Badge tone="warn">{t("code.truncated")}</Badge> : null}
        {savedFlash && !editing ? (
          <span className="text-[11px] text-ok">{t("code.saved")}</span>
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
          <div className="border-t border-white/5 px-4 py-1.5 text-[11px]">
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
function DiffLines({ patch }: { patch: string }) {
  const lines = patch.split("\n");
  return (
    <pre className="overflow-x-auto rounded-chip bg-white/[0.03] p-2 font-mono text-[11px] leading-relaxed">
      {lines.map((line, i) => {
        let cls = "text-muted-foreground";
        if (line.startsWith("+++") || line.startsWith("---")) cls = "text-foreground/50";
        else if (line.startsWith("@@")) cls = "text-accent/70";
        else if (line.startsWith("+")) cls = "text-ok";
        else if (line.startsWith("-")) cls = "text-bad";
        return (
          <div key={i} className={cls}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

/** One attempt's diffs, honestly labeled: if the attempt was reverted, a banner says the changes
 *  were undone after verification failed (they are what it ATTEMPTED, not what is on disk). */
function AttemptDiffs({ attempt, t }: { attempt: AttemptReceipt; t: TFunc }) {
  // Render when there's something real to show: a diff, or the verifier's captured output.
  if (attempt.diffs.length === 0 && !attempt.verify_output) return null;
  return (
    <div className="space-y-2 px-4 py-3">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-muted-foreground">
          {t("code.attempt")} {attempt.index}
        </span>
        {attempt.reverted ? <Badge tone="warn">↩ {t("runs.reverted")}</Badge> : null}
      </div>
      {attempt.reverted ? (
        <p className="text-[11px] text-[hsl(38_92%_62%)]">{t("code.revertedNote")}</p>
      ) : null}
      {attempt.diffs.map((diff: FileDiff) => (
        <details key={diff.path} className="rounded-chip bg-white/[0.02]" open>
          <summary className="flex cursor-pointer items-center gap-2 px-2 py-1.5 font-mono text-[11px] text-foreground/80 hover:text-foreground">
            {diff.path}
            {diff.truncated ? <Badge tone="muted">{t("code.truncated")}</Badge> : null}
          </summary>
          <div className="px-2 pb-2">
            <DiffLines patch={diff.patch} />
          </div>
        </details>
      ))}
      {/* The verifier's REAL captured stdout/stderr for this attempt (the concrete test/assert
          output). Collapsed by default; shown only when non-empty — never fabricated. */}
      {attempt.verify_output ? (
        <details className="rounded-chip bg-white/[0.02]">
          <summary className="cursor-pointer px-2 py-1.5 text-[11px] text-muted-foreground hover:text-foreground">
            {t("code.verifyOutput")}
          </summary>
          <pre className="overflow-x-auto rounded-chip bg-white/[0.03] px-2 py-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
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
    <div className="space-y-2 border-b border-white/5 px-4 py-3">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {t("code.liveEdits")}
      </div>
      {edits.map((e, i) => (
        <details key={i} className="rounded-chip bg-white/[0.02]" open>
          <summary className="flex cursor-pointer items-center gap-2 px-2 py-1.5 font-mono text-[11px] text-foreground/80 hover:text-foreground">
            <span className="shrink-0 text-muted-foreground">{i + 1}.</span>
            <span className="truncate">{e.path}</span>
          </summary>
          <div className="px-2 pb-2">
            <DiffLines patch={e.patch} />
          </div>
        </details>
      ))}
    </div>
  );
}

/** Map a live run event to a compact localized line (backend `text` is English). */
function liveLine(e: RunEvent, t: TFunc): string | null {
  if (e.kind === "status") return /planning/i.test(e.text) ? t("code.planning") : e.text;
  if (e.kind === "attempt") return `${t("code.attempt")} ${e.index} — ${t("code.verifying")}`;
  if (e.kind === "result")
    return `${t("code.attempt")} ${e.index}: ${e.success ? t("runs.passed") : t("runs.failed")}`;
  return null;
}

/** Parse plan text into display steps (strip a leading "N." / "N)"), mirroring the backend parser.
 *  Used only to render the approved plan as a numbered checklist — the backend re-parses the raw text. */
function planStepsOf(text: string): string[] {
  return text
    .split("\n")
    .map((l) => l.replace(/^\s*\d+[.)]\s*/, "").trim())
    .filter(Boolean);
}

/** A user-driven browser screenshot VERIFICATION ARTIFACT: type a URL, Capture, and the headless
 *  browser saves a full-page PNG server-side that's shown inline (same-origin `<img>`). It is an
 *  honest capture of the URL you gave — NOT a claim the agent verified anything. If the browser
 *  runtime is missing (or the page fails to load), the honest error text is shown (e.g. the
 *  "playwright install chromium" hint) — never a placeholder image. */
function VerifyPanel({ workspace }: { workspace: string }) {
  const t = useT();
  const [url, setUrl] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [shot, setShot] = useState<{ id: string; url: string; at: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function capture() {
    const target = url.trim();
    if (!target || capturing) return;
    setCapturing(true);
    setError(null);
    try {
      const res = await captureScreenshot(target, workspace || null);
      if (res.ok && res.id) {
        setShot({ id: res.id, url: target, at: new Date().toLocaleString() });
      } else {
        setShot(null);
        setError(res.error || t("code.verify.failed"));
      }
    } catch {
      setShot(null);
      setError(t("code.verify.failed"));
    } finally {
      setCapturing(false);
    }
  }

  return (
    <section className="space-y-2.5 border-t border-white/5 p-3">
      <div className="flex items-center gap-2 text-accent">
        <Camera className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">{t("code.verify.title")}</h2>
      </div>
      <p className="text-[11px] text-muted-foreground">{t("code.verify.note")}</p>
      <div className="flex flex-wrap gap-2">
        <input
          className={`${fieldCls} h-9 min-w-0 flex-1 font-mono text-xs`}
          placeholder={t("code.verify.urlPlaceholder")}
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void capture();
            }
          }}
          disabled={capturing}
        />
        <Button size="sm" disabled={!url.trim() || capturing} onClick={() => void capture()}>
          {capturing ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> {t("code.verify.capturing")}
            </>
          ) : (
            <>
              <Camera className="h-4 w-4" /> {t("code.verify.capture")}
            </>
          )}
        </Button>
      </div>
      {error ? <p className="text-[11px] text-bad">{error}</p> : null}
      {shot ? (
        <figure className="space-y-1.5">
          <img
            src={`/api/artifacts/${shot.id}`}
            alt={t("code.verify.alt")}
            className="max-w-full rounded-chip border border-white/10"
          />
          <figcaption className="text-[11px] text-muted-foreground">
            {t("code.verify.caption")} <span className="break-all font-mono">{shot.url}</span> · {shot.at}
          </figcaption>
        </figure>
      ) : null}
    </section>
  );
}

/** The right/bottom column: instruct + verify-or-revert run, then the newest run's real diffs. */
function RunPanel({
  workspace,
  onRan,
}: {
  workspace: string;
  onRan: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [task, setTask] = useState("");
  const [verify, setVerify] = useState("");
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [model, setModel] = useState("");
  const [mode, setMode] = useState<"single" | "fuse" | "cascade">("single");
  // Plan preview (makes ZERO edits): the editable plan text, whether a preview has been fetched, its
  // loading state + honest degrade note, and the steps the in-progress run is actually following.
  const [planDraft, setPlanDraft] = useState("");
  const [planPreviewed, setPlanPreviewed] = useState(false);
  const [planning, setPlanning] = useState(false);
  const [planNote, setPlanNote] = useState("");
  const [runPlan, setRunPlan] = useState<string[] | null>(null);
  const [running, setRunning] = useState(false);
  // Cooperative Stop: the in-flight run's id (from the first `run` frame) is the cancel handle, and
  // `stopping` marks that a Stop was requested (halts AFTER the current attempt — a model step can't
  // be interrupted). Both clear when the run ends.
  const [runId, setRunId] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  // The live per-edit diffs streamed during THIS run, in edit order (the play-by-play).
  const [liveEdits, setLiveEdits] = useState<{ path: string; patch: string }[]>([]);
  const [receipt, setReceipt] = useState<RunReceipt | null>(null);
  const [reverting, setReverting] = useState(false);
  const [revertErr, setRevertErr] = useState(false);
  // Whether the workspace is a git repo — gates the git-backed "Discard changes" control. Shares the
  // ["git-status", workspace] cache with the GitPanel, so a commit/revert there refreshes this too.
  const gitQ = useQuery({
    queryKey: ["git-status", workspace],
    queryFn: () => getGitStatus(workspace || null),
  });
  const isRepo = !!gitQ.data?.is_repo;

  // The run's changed paths, de-duplicated across attempts — what a git-backed discard is scoped to.
  const changedPaths = useMemo(() => {
    if (!receipt) return [];
    const set = new Set<string>();
    for (const a of receipt.attempts) for (const d of a.diffs) set.add(d.path);
    return [...set];
  }, [receipt]);

  // Accept: a successful run's changes are already on disk — just clear the pending-review UI.
  function accept() {
    setReceipt(null);
    setLines([]);
    setLiveEdits([]);
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
      setLiveEdits([]);
      setLines([t("code.git.reverted")]);
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
  // before any real run. A degrade returns empty steps + a note, never throws to the user.
  async function previewPlan() {
    if (!task.trim() || planning || running) return;
    setPlanning(true);
    setPlanNote("");
    try {
      const res = await getPlan(workspace || null, task.trim());
      setPlanDraft(res.text);
      setPlanNote(res.note);
      setPlanPreviewed(true);
    } catch {
      setPlanNote(t("code.planError"));
      setPlanPreviewed(true);
    } finally {
      setPlanning(false);
    }
  }

  // `plan`: when a non-empty string is passed, the run follows THIS approved plan verbatim (no
  // re-planning). `null`/undefined = the run plans for itself, as before.
  // Cooperative Stop: ask the backend to halt this run BEFORE its next attempt. An in-flight model
  // step can't be interrupted, so this never kills instantly — it stops after the current attempt
  // finishes. `stopping` stays set (disabling re-click) until the run's own `done` clears the state.
  async function stop() {
    if (!runId || stopping) return;
    setStopping(true);
    try {
      await cancelRun(runId);
    } catch {
      // A cancel that fails (e.g. the run just finished) is a no-op — the run's `done` clears state.
    }
  }

  function start(plan?: string | null) {
    if (!task.trim() || running) return;
    setRunning(true);
    setRunId(null);
    setStopping(false);
    setLines([]);
    setLiveEdits([]);
    setReceipt(null);
    // Only surface a task list when we injected a real plan — never fabricate steps for a plain run.
    setRunPlan(plan && plan.trim() ? planStepsOf(plan) : null);
    const append = (s: string) => setLines((prev) => [...prev, s]);
    const finish = async () => {
      setRunning(false);
      setRunId(null);
      setStopping(false);
      // The stream's `done` payload has no diffs — the newest receipt carries the real per-file diffs.
      try {
        const runs = await getRuns();
        setReceipt(runs[0] ?? null);
      } catch {
        setReceipt(null);
      }
      // The workspace may have changed (or been reverted) — refresh the tree + open file + git status.
      void qc.invalidateQueries({ queryKey: ["fs-tree"] });
      void qc.invalidateQueries({ queryKey: ["fs-file"] });
      void qc.invalidateQueries({ queryKey: ["git-status"] });
      onRan();
    };
    void streamRun(
      {
        task: task.trim(),
        verify: verify.trim() || null,
        workspace: workspace || null,
        max_attempts: maxAttempts,
        plan: plan && plan.trim() ? plan : null,
        model: model.trim() || null,
        fuse: mode === "fuse",
        cascade: mode === "cascade",
      },
      {
        onRunId: (id) => setRunId(id),
        onEvent: (e) => {
          // An `edit` frame carries a real per-file diff — collect it into the live play-by-play.
          if (e.kind === "edit" && e.path && e.patch) {
            const path = e.path;
            const patch = e.patch;
            setLiveEdits((prev) => [...prev, { path, patch }]);
            return;
          }
          const line = liveLine(e, t);
          if (line) append(line);
        },
        onDone: (d) => {
          // A cooperative Stop ends the run with stopped_reason "cancelled" — say so honestly rather
          // than reading as a plain failure.
          append(
            d.stopped_reason === "cancelled"
              ? t("code.doneCancelled")
              : d.success
                ? t("code.doneOk")
                : t("code.doneFail"),
          );
          void finish();
        },
        onError: () => {
          append(t("code.doneFail"));
          void finish();
        },
      },
    );
  }

  return (
    <aside className="flex min-h-0 flex-col overflow-auto lg:w-96">
      <div className="space-y-2.5 border-b border-white/5 p-3">
        <div className="flex items-center gap-2 text-accent">
          <Play className="h-4 w-4" />
          <h2 className="text-sm font-semibold text-foreground">{t("code.instruction")}</h2>
        </div>
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
        <div className="flex flex-wrap items-center gap-2">
          <input
            className={`${fieldCls} h-9 min-w-0 flex-1 font-mono text-xs`}
            placeholder={t("code.modelPlaceholder")}
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={running}
          />
          <div className="flex shrink-0 overflow-hidden rounded-chip border border-white/10">
            {(["single", "fuse", "cascade"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                disabled={running}
                className={cn(
                  "px-2.5 py-1.5 text-[11px] transition-colors disabled:opacity-50",
                  mode === m
                    ? "bg-accent/20 text-accent"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t(`code.mode.${m}` as const)}
              </button>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
            {t("code.maxAttempts")}
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
          <Button
            size="sm"
            variant="ghost"
            disabled={!task.trim() || running || planning}
            onClick={() => void previewPlan()}
          >
            {planning ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> {t("code.planning")}
              </>
            ) : (
              <>
                <ListChecks className="h-4 w-4" /> {t("code.previewPlan")}
              </>
            )}
          </Button>
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
              disabled={!runId || stopping}
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
        {planPreviewed && !running ? (
          <div className="space-y-2 rounded-chip border border-white/10 bg-white/[0.02] p-2.5">
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              <ListChecks className="h-3.5 w-3.5" /> {t("code.planTitle")}
            </div>
            <p className="text-[11px] text-muted-foreground">{t("code.planNote")}</p>
            {planStepsOf(planDraft).length > 0 ? (
              <ol className="space-y-1 pl-1">
                {planStepsOf(planDraft).map((step, i) => (
                  <li key={i} className="flex gap-2 text-[11px] text-foreground/80">
                    <span className="shrink-0 font-mono text-muted-foreground">{i + 1}.</span>
                    <span>{step}</span>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="text-[11px] text-muted-foreground">{t("code.planEmpty")}</p>
            )}
            {planNote ? <p className="text-[11px] text-bad">{planNote}</p> : null}
            <textarea
              className={`${fieldCls} min-h-[80px] resize-y py-2 font-mono text-[11px]`}
              placeholder={t("code.planEditPlaceholder")}
              value={planDraft}
              onChange={(e) => setPlanDraft(e.target.value)}
            />
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                disabled={!task.trim() || !planDraft.trim()}
                onClick={() => start(planDraft)}
              >
                <Play className="h-3.5 w-3.5" /> {t("code.runWithPlan")}
              </Button>
              <Button size="sm" variant="ghost" disabled={!task.trim()} onClick={() => start()}>
                {t("code.runPlain")}
              </Button>
            </div>
          </div>
        ) : null}
        {running && runPlan && runPlan.length > 0 ? (
          <div className="space-y-1 rounded-chip border border-white/10 bg-white/[0.02] p-2.5">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t("code.planForRun")}
            </div>
            <ol className="space-y-1 pl-1">
              {runPlan.map((step, i) => (
                <li key={i} className="flex gap-2 text-[11px] text-foreground/80">
                  <span className="shrink-0 font-mono text-muted-foreground">{i + 1}.</span>
                  <span>{step}</span>
                </li>
              ))}
            </ol>
          </div>
        ) : null}
        {lines.length > 0 ? (
          <div className="space-y-1 rounded-chip bg-white/[0.03] p-2 font-mono text-[11px] text-muted-foreground">
            {lines.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1">
        <LiveEdits edits={liveEdits} t={t} />
        {receipt ? (
          <>
            <div className="flex flex-wrap items-center gap-2 border-b border-white/5 px-4 py-2">
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
                <span className="text-[11px] text-bad">{t("code.git.revertError")}</span>
              ) : !isRepo ? (
                <span className="text-[11px] text-muted-foreground">{t("code.git.discardNeedsGit")}</span>
              ) : null}
            </div>
            {receipt.attempts.some((a) => a.diffs.length > 0 || a.verify_output) ? (
              <div className="divide-y divide-white/[0.04]">
                <div className="px-4 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
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
      <VerifyPanel workspace={workspace} />
    </aside>
  );
}

/** An HONEST command-runner (NOT an interactive terminal): a command input + optional cwd, streamed
 *  line by line into a scrolling <pre> (combined stdout+stderr), then the exit code. Each Run is a
 *  fresh subprocess — cwd/env don't persist between commands — so we render no fake prompt/TTY. */
function CmdRunner({ workspace }: { workspace: string }) {
  const t = useT();
  const [command, setCommand] = useState("");
  const [cwd, setCwd] = useState("");
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [exitCode, setExitCode] = useState<number | null>(null);

  function run() {
    if (!command.trim() || running) return;
    setRunning(true);
    setLines([]);
    setExitCode(null);
    void streamExec(
      { command: command.trim(), workspace: workspace || null, cwd: cwd.trim() },
      {
        onLine: (text) => setLines((prev) => [...prev, text]),
        onExit: (code) => {
          setExitCode(code);
          setRunning(false);
        },
        onError: (msg) => {
          setLines((prev) => [...prev, msg]);
          setRunning(false);
        },
      },
    );
  }

  return (
    <section className="border-t border-white/5">
      <div className="flex items-center gap-2 px-4 pt-2.5 text-accent">
        <Terminal className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">{t("code.cmdRunner")}</h2>
      </div>
      <div className="flex flex-wrap gap-2 px-4 pt-2">
        <input
          className="field h-9 min-w-0 flex-1 px-3 font-mono text-xs"
          placeholder={t("code.cmdPlaceholder")}
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              run();
            }
          }}
          disabled={running}
        />
        <input
          className="field h-9 w-56 px-3 font-mono text-xs"
          placeholder={t("code.cwd")}
          value={cwd}
          onChange={(e) => setCwd(e.target.value)}
          disabled={running}
        />
        {/* Labelled "Run command": the screen has a second, unrelated Run (the agent RunPanel), and
            an icon+"Run" alone is ambiguous to a screen reader (and to a test) about which it is. */}
        <Button
          size="sm"
          aria-label={t("code.cmdRun")}
          disabled={!command.trim() || running}
          onClick={run}
        >
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
      </div>
      <p className="px-4 pt-2 text-[11px] text-muted-foreground">{t("code.freshProcNote")}</p>
      <p className="px-4 pb-1 text-[11px] text-muted-foreground">{t("code.execSecurityNote")}</p>
      {lines.length > 0 || exitCode !== null ? (
        <pre className="mx-4 mb-3 max-h-56 overflow-auto rounded-chip bg-white/[0.03] p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {lines.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap break-all">
              {line || " "}
            </div>
          ))}
          {exitCode !== null ? (
            <div className={cn("mt-1", exitCode === 0 ? "text-ok" : "text-bad")}>
              {t("code.exit")} {exitCode}
            </div>
          ) : null}
        </pre>
      ) : null}
    </section>
  );
}

/** One changed-file row in the git panel: a clickable name (toggles its diff), its porcelain status
 *  code, and a checkbox that selects it for the explicit-path commit. */
function GitRow({
  file,
  checked,
  active,
  onToggleCheck,
  onSelect,
}: {
  file: GitFile;
  checked: boolean;
  active: boolean;
  onToggleCheck: () => void;
  onSelect: () => void;
}) {
  const code = `${file.x === " " ? "·" : file.x}${file.y === " " ? "·" : file.y}`;
  return (
    <div
      className={cn(
        "flex items-center gap-2 px-2 py-1 text-[11px]",
        active ? "bg-accent/10" : "hover:bg-white/[0.03]",
      )}
    >
      <input
        type="checkbox"
        className="h-3 w-3 shrink-0 accent-[hsl(var(--accent))]"
        checked={checked}
        onChange={onToggleCheck}
      />
      <button
        onClick={onSelect}
        title={file.path}
        className={cn(
          "min-w-0 flex-1 truncate text-left font-mono",
          active ? "text-accent" : "text-foreground/80 hover:text-foreground",
        )}
      >
        {file.path}
      </button>
      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{code}</span>
    </div>
  );
}

/** The git panel: real `git status` grouped by staged / modified / untracked, a per-file diff on
 *  click, and a commit box that stages the EXPLICITLY selected paths (never `git add -A`). When the
 *  folder isn't a git repo (or git is missing), an honest empty-state invites `git init`. */
function GitPanel({ workspace }: { workspace: string }) {
  const t = useT();
  const qc = useQueryClient();
  const statusQ = useQuery({
    queryKey: ["git-status", workspace],
    queryFn: () => getGitStatus(workspace || null),
  });
  const status = statusQ.data;
  const files = useMemo(() => status?.files ?? [], [status]);
  const staged = files.filter((f) => f.staged);
  const modified = files.filter((f) => !f.staged && !f.untracked);
  const untracked = files.filter((f) => f.untracked);

  const [message, setMessage] = useState("");
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [selected, setSelected] = useState<{ path: string; staged: boolean } | null>(null);
  const [committing, setCommitting] = useState(false);
  const [commitErr, setCommitErr] = useState(false);
  const [commitHash, setCommitHash] = useState<string | null>(null);

  // Default-select the modified + untracked paths whenever the changed-file set changes.
  const filesKey = files.map((f) => `${f.path}:${f.staged}`).join("\n");
  useEffect(() => {
    const next: Record<string, boolean> = {};
    for (const f of files) next[f.path] = !f.staged; // modified/untracked default-checked
    setChecked(next);
    setSelected(null);
    setCommitHash(null);
    setCommitErr(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filesKey]);

  const diffQ = useQuery({
    queryKey: ["git-diff", workspace, selected?.path, selected?.staged],
    queryFn: () => getGitDiff(workspace || null, selected!.path, selected!.staged),
    enabled: selected !== null,
  });

  const selectedPaths = files.filter((f) => checked[f.path]).map((f) => f.path);

  async function commit() {
    if (!message.trim() || selectedPaths.length === 0 || committing) return;
    setCommitting(true);
    setCommitErr(false);
    setCommitHash(null);
    try {
      const res = await gitCommit(workspace || null, message.trim(), selectedPaths);
      if (res.ok) {
        setCommitHash(res.commit);
        setMessage("");
        await qc.invalidateQueries({ queryKey: ["git-status", workspace] });
        void qc.invalidateQueries({ queryKey: ["fs-tree"] });
      } else {
        setCommitErr(true);
      }
    } catch {
      setCommitErr(true);
    } finally {
      setCommitting(false);
    }
  }

  function toggle(path: string) {
    setChecked((prev) => ({ ...prev, [path]: !prev[path] }));
  }
  function pick(path: string, isStaged: boolean) {
    setSelected((prev) => (prev?.path === path && prev.staged === isStaged ? null : { path, staged: isStaged }));
  }

  function group(label: string, list: GitFile[], isStaged: boolean) {
    if (list.length === 0) return null;
    return (
      <div>
        <div className="px-2 pt-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </div>
        {list.map((f) => (
          <GitRow
            key={`${isStaged ? "s" : "w"}:${f.path}`}
            file={f}
            checked={!!checked[f.path]}
            active={selected?.path === f.path && selected.staged === isStaged}
            onToggleCheck={() => toggle(f.path)}
            onSelect={() => pick(f.path, isStaged)}
          />
        ))}
      </div>
    );
  }

  return (
    <section className="border-t border-white/5">
      <div className="flex items-center gap-2 px-4 pt-2.5 text-accent">
        <GitBranch className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">{t("code.git.title")}</h2>
        {status?.is_repo ? (
          <span className="font-mono text-[11px] text-muted-foreground">
            {t("code.git.branch")}: {status.branch || "—"}
          </span>
        ) : null}
      </div>

      {statusQ.isLoading ? (
        <div className="flex justify-center py-4 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
        </div>
      ) : !status?.is_repo ? (
        <p className="px-4 py-3 text-[11px] text-muted-foreground">{t("code.git.notRepo")}</p>
      ) : files.length === 0 ? (
        <p className="px-4 py-3 text-[11px] text-muted-foreground">{t("code.git.clean")}</p>
      ) : (
        <div className="flex flex-col gap-2 px-2 py-2 lg:flex-row lg:items-start">
          <div className="min-w-0 flex-1 rounded-chip bg-white/[0.02] py-1">
            {group(t("code.git.staged"), staged, true)}
            {group(t("code.git.modified"), modified, false)}
            {group(t("code.git.untracked"), untracked, false)}
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            <input
              className="field h-9 px-3 text-xs"
              placeholder={t("code.git.commitMsg")}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              disabled={committing}
            />
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={!message.trim() || selectedPaths.length === 0 || committing}
                onClick={() => void commit()}
              >
                {committing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                {t("code.git.commit")} ({selectedPaths.length})
              </Button>
              {commitHash ? (
                <span className="font-mono text-[11px] text-ok">
                  {t("code.git.committed")} {commitHash}
                </span>
              ) : null}
              {commitErr ? <span className="text-[11px] text-bad">{t("code.git.commitError")}</span> : null}
            </div>
          </div>
        </div>
      )}

      {selected ? (
        <div className="px-4 pb-3">
          {diffQ.isLoading ? (
            <div className="py-2 text-[11px] text-muted-foreground">…</div>
          ) : diffQ.data?.patch ? (
            <DiffLines patch={diffQ.data.patch} />
          ) : (
            <p className="text-[11px] text-muted-foreground">{t("code.noDiff")}</p>
          )}
        </div>
      ) : null}

      <p className="px-4 pb-2 text-[11px] text-muted-foreground">{t("code.git.gitNote")}</p>
    </section>
  );
}

export function Code() {
  const t = useT();
  const qc = useQueryClient();
  const [workspace, setWorkspace] = useState("");
  const [openFile, setOpenFile] = useState<string | null>(null);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 border-b border-white/5 px-5 py-3 text-accent">
        <FileCode2 className="h-5 w-5" />
        <h1 className="text-sm font-semibold text-foreground">{t("code.title")}</h1>
      </div>
      <p className="border-b border-white/5 px-5 py-2 text-[11px] text-muted-foreground">
        {t("code.safetyNote")}
      </p>
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <TreePanel
          workspace={workspace}
          onWorkspace={(ws) => {
            setWorkspace(ws);
            setOpenFile(null);
            void qc.invalidateQueries({ queryKey: ["fs-tree"] });
            void qc.invalidateQueries({ queryKey: ["git-status"] });
          }}
          activePath={openFile}
          onOpen={setOpenFile}
        />
        <Viewer workspace={workspace} path={openFile} />
        <RunPanel
          workspace={workspace}
          onRan={() => {
            // Re-read the currently open file after the agent edited/reverted the workspace.
            if (openFile) void qc.invalidateQueries({ queryKey: ["fs-file", workspace, openFile] });
          }}
        />
      </div>
      <GitPanel workspace={workspace} />
      <CmdRunner workspace={workspace} />
      <p className="border-t border-white/5 px-5 py-2 text-[11px] text-muted-foreground">
        {t("code.phase2note")}
      </p>
    </div>
  );
}
