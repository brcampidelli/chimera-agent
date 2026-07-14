import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import hljs from "highlight.js";
import {
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  FileCode2,
  Folder,
  FolderOpen,
  Loader2,
  Pencil,
  Play,
  Save,
  Terminal,
  X,
} from "lucide-react";
import { getFsFile, getFsTree, getRuns, saveFile, streamExec, streamRun, type RunEvent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useT, type TFunc } from "@/lib/i18n";
import type { AttemptReceipt, FileDiff, FsNode, RunReceipt } from "@/lib/types";
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
  if (attempt.diffs.length === 0) return null;
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
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [receipt, setReceipt] = useState<RunReceipt | null>(null);

  function start() {
    if (!task.trim() || running) return;
    setRunning(true);
    setLines([]);
    setReceipt(null);
    const append = (s: string) => setLines((prev) => [...prev, s]);
    const finish = async () => {
      setRunning(false);
      // The stream's `done` payload has no diffs — the newest receipt carries the real per-file diffs.
      try {
        const runs = await getRuns();
        setReceipt(runs[0] ?? null);
      } catch {
        setReceipt(null);
      }
      // The workspace may have changed (or been reverted) — refresh the tree + open file.
      void qc.invalidateQueries({ queryKey: ["fs-tree"] });
      void qc.invalidateQueries({ queryKey: ["fs-file"] });
      onRan();
    };
    void streamRun(
      {
        task: task.trim(),
        verify: verify.trim() || null,
        workspace: workspace || null,
        max_attempts: maxAttempts,
      },
      {
        onEvent: (e) => {
          const line = liveLine(e, t);
          if (line) append(line);
        },
        onDone: (d) => {
          append(d.success ? t("code.doneOk") : t("code.doneFail"));
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
        <div className="flex items-center gap-3">
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
          <Button size="sm" disabled={!task.trim() || running} onClick={start}>
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
        {lines.length > 0 ? (
          <div className="space-y-1 rounded-chip bg-white/[0.03] p-2 font-mono text-[11px] text-muted-foreground">
            {lines.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        ) : null}
      </div>

      <div className="min-h-0 flex-1">
        {receipt ? (
          receipt.attempts.some((a) => a.diffs.length > 0) ? (
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
          )
        ) : null}
      </div>
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
        <Button size="sm" disabled={!command.trim() || running} onClick={run}>
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
      <CmdRunner workspace={workspace} />
      <p className="border-t border-white/5 px-5 py-2 text-[11px] text-muted-foreground">
        {t("code.phase2note")}
      </p>
    </div>
  );
}
