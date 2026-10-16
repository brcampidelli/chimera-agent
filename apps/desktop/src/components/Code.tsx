import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import hljs from "highlight.js";
import {
  ChevronDown,
  FileCode2,
  Folder,
  FolderGit2,
  Loader2,
  MessageSquare,
  Minus,
  Pencil,
  Save,
  Terminal,
  X,
} from "lucide-react";
import {
  getConfig,
  getFsFile,
  getFsImage,
  grantCodeProjectShell,
  listCodeSessions,
  saveFile,
  type Approval,
  type CodeSessionMeta,
  type Profile,
  type Reach,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { focusRing } from "@/components/ui/focus";
import { useOptionalToast } from "@/components/ui/toast";
import { Agents } from "@/components/Agents";
import { Conversation } from "@/components/code/Conversation";
import { PostureNote } from "@/components/code/PostureNote";
import { ModelPicker } from "@/components/code/ModelPicker";
import { ProviderPicker } from "@/components/code/ProviderPicker";
import { StylePicker, styleLabel } from "@/components/code/StylePicker";
import { Tooltip } from "@/components/ui/tooltip";
import {
  NO_OVERRIDE,
  RolesBar,
  toRoleModels,
  type RoleOverride,
} from "@/components/code/RolesBar";
import { SessionSidebar, sidebarSessionOrder } from "@/components/code/SessionSidebar";
import { FindBar } from "@/components/code/FindBar";
import { EdgeTab, useRegionEnter } from "@/components/shell/RegionToggle";
import { Splitter } from "@/components/shell/Splitter";
import { Dock } from "@/components/shell/Dock";
import { MaximizeButton } from "@/components/shell/Maximize";
import { ComposerSettings } from "@/components/code/ComposerSettings";
import { HtmlPreview } from "@/components/code/HtmlPreview";
import { ProjectPicker } from "@/components/code/ProjectPicker";
import { ProjectPackCard } from "@/components/code/ProjectPackCard";
import { useRunSession } from "@/lib/run-session";
import { useNum, useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import type { OutputStyle } from "@/lib/types";
import { cn } from "@/lib/utils";
import { shellGranted } from "@/lib/project-shell";
import { loadProjects } from "@/lib/projects";
import { readLastSession, readWorkspace, writeLastSession, writeWorkspace } from "@/lib/workspace";
import { useAppCommand } from "@/lib/appCommands";
import { rememberClosed, takeClosed } from "@/lib/closedConversations";
import { useConversationHotkeys } from "@/lib/hotkeys";
import { CONVERSATION_WINDOW_FEATURES, conversationUrl, conversationWindowName } from "@/lib/float/protocol";

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

/** Extensions the byte endpoint will serve, mirroring `_IMAGE_MEDIA_TYPES` in `chimera/api/fs_api.py`.
 *
 * Duplicated rather than fetched because this list decides only whether to ASK. The server decides
 * what it will hand back, and a stale copy here can at worst produce a request that comes back 415 —
 * never a file served on this list's say-so. Widening this without widening the server's changes
 * nothing about what is served, which is the property that makes the duplication safe.
 *
 * SVG is absent for the same reason it is absent server-side: it is script-capable under a top-level
 * navigation, and it needs nothing here anyway — an SVG is text, so the highlighted view already
 * shows it. */
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp"]);

/** Whether this file is a page rather than code that happens to be markup. */
function isHtmlPath(path: string): boolean {
  const ext = path.includes(".") ? path.split(".").pop()!.toLowerCase() : "";
  return ext === "html" || ext === "htm";
}

/** Prose formats, wrapped rather than scrolled sideways.
 *
 * A markdown paragraph is one long line, and the viewer is a column a few hundred pixels wide: left
 * unwrapped, every sentence ran off the right edge and the file was read by scrolling sideways line by
 * line. Code keeps its lines whole, because there a wrapped line would look like two and indentation
 * is meaning. */
const PROSE_EXTS = new Set(["md", "markdown", "mdx", "txt", "rst"]);

function isProsePath(path: string): boolean {
  const ext = path.includes(".") ? path.split(".").pop()!.toLowerCase() : "";
  return PROSE_EXTS.has(ext);
}

/** The same quiet icon button the cards and the dock panels carry in their corner. */
const iconButton = cn(
  "rounded-sm p-0.5 text-muted-foreground transition-colors duration-1 ease-out",
  "hover:bg-surface-hover hover:text-foreground",
  focusRing,
);

function isImagePath(path: string): boolean {
  const ext = path.includes(".") ? path.split(".").pop()!.toLowerCase() : "";
  return IMAGE_EXTS.has(ext);
}

/** The chart the agent just drew, on the screen.
 *
 * `render_chart` and `generate_image` write into the workspace and the viewer answered "binary or
 * non-text" — our own app could not display the output of our own tools, in ten languages.
 *
 * The bytes arrive through `fetch` and become an object URL rather than going straight into
 * `<img src>`, because the request has to carry the bearer token in a header and an `<img>` cannot
 * send one. Putting the token in the query string instead would write it into every access log.
 *
 * The URL is revoked when the file changes or the panel unmounts: an object URL pins its blob in
 * memory until it is released, so a session spent clicking through screenshots would otherwise
 * accumulate every one of them. */
function ImagePreview({ workspace, path }: { workspace: string; path: string }) {
  const t = useT();
  const q = useQuery({
    queryKey: ["fs-image", workspace, path],
    queryFn: () => getFsImage(workspace || null, path),
  });
  const blob = q.data;
  const [url, setUrl] = useState("");

  useEffect(() => {
    if (!blob) {
      setUrl("");
      return;
    }
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);

  if (q.isError) {
    // Named separately from the generic binary note: "we could not load this image" is a different
    // fact from "this is not an image", and answering the second when the first happened would send
    // someone looking for a bug in a file that is fine.
    return <div className="px-4 py-6 text-sm text-bad-foreground">{t("code.imageError")}</div>;
  }
  if (!url) {
    return (
      <div className="flex justify-center py-10 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }
  return (
    <div className="p-4">
      <img
        src={url}
        alt={t("code.imageAlt", { path })}
        className="max-w-full rounded-chip border border-hairline"
      />
    </div>
  );
}

/** Mirrors `MAX_VIEW_CHARS` in chimera/api/fs_api.py: the most the viewer asks for at once. */
const VIEW_ALL_CHARS = 1_000_000;

/** The center column: a syntax-highlighted viewer with an opt-in editor. Read-only is the default;
 *  "Edit" swaps in a mono textarea → Save PUTs it (atomic + newline-preserving + size-capped
 *  server-side). Truncated/binary files are NOT editable (saving would clobber the unseen remainder).
 *
 *  It minimises to its header and closes, with the controls and words the cards and the dock panels
 *  already use: it could only be maximised, so a file opened once held a third of the window until
 *  the conversation was switched. */
function Viewer({
  workspace,
  path,
  minimized = false,
  onMinimize,
  onClose,
}: {
  workspace: string;
  path: string | null;
  minimized?: boolean;
  onMinimize?: (minimized: boolean) => void;
  onClose?: () => void;
}) {
  const t = useT();
  const panelName = t("layout.panel.viewer");
  const qc = useQueryClient();
  // The first 20,000 characters, as the agent reads them; the rest on request (plan, 2026-10-10:
  // a long file showed a "truncated" badge and no way to read further).
  const [whole, setWhole] = useState(false);
  const num = useNum();
  useEffect(() => setWhole(false), [path]);
  const q = useQuery({
    queryKey: ["fs-file", workspace, path, whole],
    queryFn: () =>
      whole
        ? getFsFile(workspace || null, path as string, VIEW_ALL_CHARS)
        : getFsFile(workspace || null, path as string),
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
  // A document's text preview is never editable either: saving it would replace the .docx with
  // the text of the .docx.
  const editable =
    path !== null && !!q.data && !q.data.note && !q.data.truncated && !q.data.document;

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
    <section
      data-panel="viewer"
      aria-label={panelName}
      className={cn("flex min-h-0 flex-col border-hairline lg:border-r", minimized ? "flex-none" : "flex-1")}
    >
      <div className={cn("flex items-center gap-2 px-4 py-2.5", !minimized && "border-b border-hairline")}>
        <FileCode2 className="h-4 w-4 shrink-0 text-accent" />
        <span
          className="min-w-0 flex-1 truncate font-mono text-xs text-foreground"
          title={path ?? undefined}
        >
          {path ?? t("code.noFile")}
        </span>
        {dirty ? <Badge tone="warn">{t("code.dirty")}</Badge> : null}
        {q.data?.truncated ? <Badge tone="warn">{t("code.truncated")}</Badge> : null}
        {savedFlash && !editing ? (
          <span className="text-xs text-ok-foreground">{t("code.saved")}</span>
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
        <div className="flex shrink-0 items-center gap-0.5">
          {onMinimize ? (
            <Tooltip label={t(minimized ? "layout.card.expand" : "layout.card.minimize", { name: panelName })}>
              <button
                type="button"
                aria-label={t(minimized ? "layout.card.expand" : "layout.card.minimize", { name: panelName })}
                aria-expanded={!minimized}
                onClick={() => onMinimize(!minimized)}
                className={iconButton}
              >
                {minimized ? <ChevronDown className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
              </button>
            </Tooltip>
          ) : null}
          <MaximizeButton panel="viewer" name={panelName} />
          {onClose ? (
            <Tooltip label={t("layout.card.close", { name: panelName })}>
              <button
                type="button"
                aria-label={t("layout.card.close", { name: panelName })}
                onClick={onClose}
                className={iconButton}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </Tooltip>
          ) : null}
        </div>
      </div>
      {/* On its own row, not in the header: beside the badge and the panel's controls it squeezed
          the file name to its first letters in a narrow viewer (0.65.0 visual check). */}
      {!minimized && q.data?.truncated && !whole && q.data.total_chars ? (
        <div className="flex justify-end border-b border-hairline px-4 py-1.5">
          <Button size="sm" variant="outline" onClick={() => setWhole(true)}>
            {t("code.showWhole", {
              n: num(Math.min(q.data.total_chars, VIEW_ALL_CHARS)),
            })}
          </Button>
        </div>
      ) : null}
      {/* Minimised, the header is the whole pane. The editor stays mounted behind it, so a draft
          survives being put away and brought back. */}
      {minimized ? null : editing ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <textarea
            className="min-h-0 flex-1 resize-none bg-transparent p-4 font-mono text-sm leading-relaxed text-foreground outline-hidden"
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
            <div className="px-4 py-6 text-sm text-bad-foreground">{t("code.fileError")}</div>
          ) : q.data?.document ? (
            // A PDF, Word, Excel or PowerPoint file, as its text — what `create_document` writes and
            // what "Open beside" on a receipt opens. Labelled a preview because it is one: layout,
            // images and formatting are not here, and a reader who took this for the file would be
            // misled about what the owner is about to send someone.
            <div className="p-4">
              <p className="mb-2 text-xs text-muted-foreground">
                {q.data.content
                  ? t("code.documentPreview", { format: q.data.document.toUpperCase() })
                  : t("code.documentNoPreview", { format: q.data.document.toUpperCase() })}
              </p>
              {q.data.content ? (
                <pre className="whitespace-pre-wrap text-sm text-foreground">{q.data.content}</pre>
              ) : null}
            </div>
          ) : q.data?.note && path !== null && isImagePath(path) ? (
            <ImagePreview workspace={workspace} path={path} />
          ) : q.data?.note ? (
            // Still the honest answer for a .zip or a .wasm: there is no way to show those, and an
            // <img> pointed at one renders a broken-image icon, which claims a failure that did not
            // happen.
            <div className="px-4 py-6 text-sm text-muted-foreground">{t("code.binaryNote")}</div>
          ) : path !== null && isHtmlPath(path) && q.data?.content ? (
            // A page, as a page. Source is one click away inside the component — the toggle lives
            // there because the preview is what someone opening an .html file came to see.
            <HtmlPreview workspace={workspace} path={path} source={q.data.content} />
          ) : (
            <pre
              className={cn(
                "p-4 text-sm leading-relaxed",
                isProsePath(path ?? "") ? "whitespace-pre-wrap break-words" : "overflow-x-auto",
              )}
            >
              <code className="hljs bg-transparent" dangerouslySetInnerHTML={{ __html: html }} />
            </pre>
          )}
        </div>
      )}
    </section>
  );
}

export function Code() {
  const t = useT();
  // The session list is the left region (dynamic screen, phase 1). The parent owns its enter
  // animation because it stays mounted while the list comes and goes.
  const { layout } = useLayout();
  const showSessions = layout.regions.left.visible;
  const sessionsEnter = useRegionEnter(showSessions, "left");
  // The file viewer maximised (phase 5): it takes the whole row, and the list and the conversation step
  // aside until it is restored (its own button, or Escape).
  const { dispatch: layoutDispatch } = useLayout();
  const viewerMax = layout.maximized === "viewer";
  const qc = useQueryClient();
  // Lazy initialiser, not `useState(readWorkspace())`: the latter reads storage on every render.
  const [workspace, setWorkspace] = useState(readWorkspace);
  const [openFile, setOpenFile] = useState<string | null>(null);
  // A maximised viewer with no file is a blank screen: closing the file restores the layout, so the next
  // file does not open maximised without anyone asking.
  useEffect(() => {
    if (!openFile && viewerMax) layoutDispatch({ type: "maximize", panel: null });
  }, [openFile, viewerMax, layoutDispatch]);
  // Minimised or closed, the viewer's state lives in the layout like every other panel's, so the
  // status bar's hidden tray lists a closed viewer with "Show" and the layout's undo covers both.
  // It is a state of the FILE on screen, though: with no file open it goes back to open, so a viewer
  // closed yesterday does not swallow the next file someone opens, and the tray never offers to show
  // an empty pane.
  const viewerMode = layout.panels.viewer.mode;
  const viewerClosed = openFile !== null && viewerMode === "closed";
  const viewerMin = openFile !== null && viewerMode === "minimized";
  // The viewer as a pane with a body. Only that one takes the conversation's place while stacked:
  // minimised it is a header under the conversation, closed it is nothing.
  const viewerFull = openFile !== null && viewerMode === "open";
  useEffect(() => {
    if (!openFile && viewerMode !== "open") layoutDispatch({ type: "set-mode", panel: "viewer", mode: "open" });
  }, [openFile, viewerMode, layoutDispatch]);
  // Maximising a minimised viewer means wanting to read it: it opens, rather than filling the screen
  // with a header.
  useEffect(() => {
    if (viewerMax && viewerMode === "minimized") layoutDispatch({ type: "set-mode", panel: "viewer", mode: "open" });
  }, [viewerMax, viewerMode, layoutDispatch]);
  const toast = useOptionalToast();
  /** Opening a file is asking to see it: whatever the viewer was put away as, it comes back open. */
  const showFile = useCallback(
    (path: string) => {
      setOpenFile(path);
      layoutDispatch({ type: "set-mode", panel: "viewer", mode: "open" });
    },
    [layoutDispatch],
  );
  const closeViewer = useCallback(() => {
    if (!layoutDispatch({ type: "set-mode", panel: "viewer", mode: "closed" })) return;
    const name = t("layout.panel.viewer");
    toast(t("layout.card.closed", { name }), "info", {
      label: t("layout.undo"),
      run: () => void layoutDispatch({ type: "set-mode", panel: "viewer", mode: "open" }),
    });
  }, [layoutDispatch, t, toast]);
  const [projectDraft, setProjectDraft] = useState(readWorkspace);
  // Which stored conversation is on screen, and a key that remounts the transcript when it
  // changes — the conversation holds its exchanges in state, so switching sessions has to
  // discard them rather than let the previous project's turns sit above the new one.
  const [picking, setPicking] = useState(false);
  // The project's last conversation, read when the screen opens: it used to start at null, so
  // leaving the screen and coming back landed on a blank conversation. Only READ here and when the
  // project changes — the conversation on screen records a new one itself, and pushing that back
  // into `resumeSession` would reload the stored transcript over the turn it is streaming.
  const [sessionId, setSessionId] = useState<string | null>(() => readLastSession(readWorkspace()));
  const [conversationKey, setConversationKey] = useState(0);
  // The conversation and the run share one workspace, so they share two facts: what the user asked
  // (handed over by "Run with verification") and whether a run is already in flight.
  // A confirmed decomposition. Keyed by `at` so a second batch is a new board rather than a restart
  // of the old one — the board starts its runs on mount, which is only correct if mounting is what a
  // new batch does.
  const [batch, setBatch] = useState<{ tasks: string[]; at: number } | null>(null);
  // Read from the shared session rather than from the panel below, which is about to be deleted.
  // This is strictly WIDER than what it replaces: the session also sees runs launched from the Work
  // screen, which the local flag never did — so the conversation now refuses to send while ANY run
  // is writing in this workspace, not just one started here.
  // This project's run: runs work in several projects at once, one per project.
  const run = useRunSession(workspace);
  // Only when the run is in THIS project. A run elsewhere cannot race this workspace, and
  // blocking on it would be a lie about why. A run with no workspace still blocks: not
  // knowing which directory it is editing is a reason to be careful, not a reason to allow.
  const runBusy = run.running && (run.workspace === null || run.workspace === workspace);
  // Not chosen here, and not chosen before typing either: what the agent may do is a standing
  // decision, so it lives in Settings and this screen reads it. The fallbacks are the pair this was
  // hardcoded to — edit the workspace, no shell, stop and ask if the run read something untrusted —
  // so an install that has configured nothing behaves exactly as it did.
  //
  // Still SENT on every request rather than omitted: omitting resolves to no tool denials and no
  // pause at all, which is more permissive than any corner someone could have picked. The server
  // applies the configured posture as a floor regardless; sending it keeps the two in agreement, so
  // the posture line in the transcript describes the run that is actually happening.
  const cfg = useQuery({ queryKey: ["config"], queryFn: getConfig });
  // Whether the agent may run commands IN THIS PROJECT. The setting in Settings is global — it was
  // the only lever, so the honest choices were "no project may" and "every project may". Running
  // the tests and `npm install` is what separates writing files from building something that works,
  // and granting that for the folder you are in is a different decision from granting it for every
  // folder you open next.
  //
  // READ from the server's record, never asserted: the server holds every turn to that record, so a
  // copy kept here could only ever disagree with it. Same query key as the sidebar and the Folders
  // card, so a switch flipped in one place is the state in all three.
  const projectRows = useQuery({ queryKey: ["code-projects"], queryFn: loadProjects });
  const shellHere = shellGranted(projectRows.data, workspace);
  const grantShell = useMutation({
    mutationFn: (granted: boolean) => grantCodeProjectShell(workspace, granted),
    onSuccess: (rows) => qc.setQueryData(["code-projects"], rows),
  });

  const configured = (cfg.data?.autonomy.reach || "workspace") as Reach;
  // The project's grant RAISES the reach; it never lowers what the owner configured. Somebody who
  // set `read_only` system-wide means it, and a per-project checkbox is not the place to overrule
  // a standing decision — the server agrees, and refuses `CHIMERA_HOST_EXEC=deny` outright.
  const reach: Reach =
    shellHere && configured === "workspace" ? "workspace_shell" : configured;
  const approval = (cfg.data?.autonomy.approval || "suspicious") as Approval;
  const posture = useMemo(() => ({ reach, approval }), [reach, approval]);
  // Who does the work. Session-local rather than persisted: handing your workspace to another
  // company's agent is a decision per piece of work, not a setting that quietly stays on from the
  // last time — and a persisted default here would be the app choosing on the user's behalf.
  const [provider, setProvider] = useState("");
  // Which model answers, for this conversation. Session-local for the same reason as `provider` and
  // the spend ceiling: a model quietly carried over from a week ago is how a turn ends up costing
  // thirty times what the person expected. "" means the install's default, and the picker offers to
  // make a pick the standing default — which is the same intent, stated rather than accumulated.
  const [model, setModel] = useState("");
  // How this conversation's answers are written (study 29, P4.5). Per conversation and reset with the
  // model below, for the model's reason: a style carried over from last week is a choice nobody
  // remembers making. "default" is never sent, so until someone picks one nothing changes.
  const [style, setStyle] = useState<OutputStyle>("default");
  // Which model does which job. Session-local, like `provider` and `model` above and for the same
  // reason: routing that quietly carried over from last week is how a turn costs what nobody
  // expected.
  //
  // It was `const profile = "balanced"` with a comment admitting there was no picker on any screen
  // — and `RolesBar`, which IS that picker and carries a `compact` mode documented as "for the
  // composer strip", existed with exactly one occurrence in the whole source: its own definition.
  // A control written for a place it was never put, so "economy" and "max" were unreachable words.
  //
  // A run started below still reports `profile_source: "system"` when nobody touched this, so the
  // cost panel keeps counting a default apart from a profile somebody deliberately chose.
  const [profile, setProfile] = useState<Profile>("balanced");
  const [profileTouched, setProfileTouched] = useState(false);
  // Which MODEL does which job, over the profile's answer. The bar has rendered read-only here
  // since it arrived — `RolesBar` shows the tiers and nothing else when the caller omits these two
  // props — while the identical bar on the Runs screen has been fully editable. Same component,
  // same backend field (`CodeSeams.roles`), one screen allowed to use it.
  const [roles, setRoles] = useState<RoleOverride>(NO_OVERRIDE);
  const [oneModel, setOneModel] = useState(false);

  /** The conversation on screen. `sessionId` is the one the screen OPENED; a new conversation records
   *  its own id in the stored last session once its first turn is saved, and never pushes it back
   *  here (see `sessionId` above), so the stored value is where a just-started one is found. */
  const onScreenSession = useCallback(
    () => sessionId ?? readLastSession(workspace),
    [sessionId, workspace],
  );
  /** Put the conversation on screen on the closed stack, for ⌘⇧T. Called by every way of putting a
   *  different conversation in its place — the four used to be inline, and a fifth way to leave
   *  that forgot to call this would silently make that conversation unreopenable. */
  const leaveConversation = useCallback(() => {
    const id = onScreenSession();
    if (id) rememberClosed({ id, workspace });
  }, [onScreenSession, workspace]);

  /** Change the project this screen is working in. One function, because it was three near-copies.
   *
   * Clearing `sessionId` is the part all three were missing, and it is the part that matters: the
   * server fixes a conversation's project when the conversation is created and never moves it, so
   * carrying the id across a project change left the next turn writing into a conversation filed
   * under the OLD project. The screen said one thing and the disk said another, and the disk was
   * right. Invisible with one workspace; routine with several.
   *
   * Resuming a conversation is deliberately NOT this: it also changes the project, but it is
   * arriving at an existing conversation rather than leaving one.
   */
  const switchProject = useCallback(
    (next: string) => {
      if (next === workspace) return;
      leaveConversation();
      setWorkspace(next);
      writeWorkspace(next);
      setProjectDraft(next);
      // That project's own conversation, or a new one: never the one from the project being left.
      setSessionId(readLastSession(next));
      startConversation();
      void qc.invalidateQueries({ queryKey: ["fs-file"] });
      void qc.invalidateQueries({ queryKey: ["git-status"] });
    },
    [qc, workspace, leaveConversation],
  );

  /** Everything that has to be true at the START of a conversation.
   *
   *  The model pick says "Applies to this conversation" and is described in code as session-local,
   *  "because a model quietly carried over from a week ago is how a turn ends up costing thirty
   *  times what the person expected". It was screen state: it survived New conversation, Resume and
   *  a project switch, so the chip said one scope and the state had another. The fusion cast was
   *  already correct — remounted by `key={conversationKey}` — which is what made the two disagree.
   *
   *  One function because it was three near-copies, and the copy that forgot a line is exactly how
   *  this happened.
   */
  const startConversation = useCallback(() => {
    setConversationKey((n) => n + 1);
    setOpenFile(null);
    setModel("");
    setStyle("default");
  }, []);

  /** Open a stored conversation: from the sidebar, from ⌘⇧T, from Ctrl+Tab. One function because
   *  the shortcuts must land exactly where a click on the row lands. */
  const resumeConversation = useCallback(
    (session: Pick<CodeSessionMeta, "id" | "workspace">) => {
      if (session.id !== onScreenSession()) leaveConversation();
      setSessionId(session.id);
      writeLastSession(session.workspace, session.id);
      startConversation();
      if (session.workspace !== workspace) {
        setWorkspace(session.workspace);
        writeWorkspace(session.workspace);
        setProjectDraft(session.workspace);
      }
    },
    [onScreenSession, leaveConversation, startConversation, workspace],
  );

  // The sidebar's own queries (same keys, so the same cache): the order Ctrl+Tab walks, and whether a
  // closed conversation still exists before ⌘⇧T reopens it.
  const storedSessions = useQuery({ queryKey: ["code-sessions"], queryFn: listCodeSessions });
  const reopenClosed = useCallback(() => {
    const known = storedSessions.data;
    // Before the list has loaded nothing can be called deleted, so nothing is skipped for it.
    const exists = (id: string) => known === undefined || known.some((s) => s.id === id);
    const closed = takeClosed(exists, onScreenSession());
    if (closed) resumeConversation(closed);
  }, [storedSessions.data, onScreenSession, resumeConversation]);
  const stepConversation = useCallback(
    (dir: 1 | -1) => {
      const order = sidebarSessionOrder(storedSessions.data ?? [], projectRows.data ?? [], workspace);
      if (order.length === 0) return;
      const at = order.findIndex((s) => s.id === onScreenSession());
      // Wraps at both ends, like tabs. From a conversation not in the list yet (a new one, before
      // its first save), forward is the top of the list and back is the bottom.
      const next = at < 0 ? (dir === 1 ? 0 : order.length - 1) : (at + dir + order.length) % order.length;
      if (order[next].id !== onScreenSession()) resumeConversation(order[next]);
    },
    [storedSessions.data, projectRows.data, workspace, onScreenSession, resumeConversation],
  );

  // Find in the conversation. `findFocus` bumps on every ⌘F, so a second press puts the cursor back
  // in the field rather than doing nothing; `findStep` carries F3 and ⇧F3 when focus is elsewhere.
  const [findOpen, setFindOpen] = useState(false);
  const [findFocus, setFindFocus] = useState(0);
  const [findStep, setFindStep] = useState<{ n: number; dir: 1 | -1 }>({ n: 0, dir: 1 });
  const transcriptRoot = useRef<HTMLElement | null>(null);
  const openFind = useCallback(() => {
    setFindOpen(true);
    setFindFocus((n) => n + 1);
  }, []);
  // A different conversation is a different transcript: the bar's matches would point into nothing.
  useEffect(() => setFindOpen(false), [conversationKey]);

  useConversationHotkeys({
    onReopen: reopenClosed,
    onPrevious: () => stepConversation(-1),
    onNext: () => stepConversation(1),
    onFind: openFind,
    onFindNext: findOpen ? () => setFindStep((s) => ({ n: s.n + 1, dir: 1 })) : undefined,
    onFindPrevious: findOpen ? () => setFindStep((s) => ({ n: s.n + 1, dir: -1 })) : undefined,
  });
  useAppCommand("reopenConversation", reopenClosed);
  useAppCommand("previousConversation", () => stepConversation(-1));
  useAppCommand("nextConversation", () => stepConversation(1));
  useAppCommand("find", openFind);

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
          switchProject(projectDraft.trim());
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
            everyone else.

            `type="button"` is load-bearing, not tidiness. A `<button>` inside a `<form>` submits it
            by default, so this one ran `switchProject(projectDraft)` as well as opening the picker
            — and the natural way to reach for it is with an EMPTY field, because not wanting to
            type is the reason you clicked. That switched the root to nothing and started a new
            conversation before showing a single folder to choose from. Choosing is a navigation
            gesture; it must not decide anything. */}
        <Button size="sm" type="button" variant="ghost" onClick={() => setPicking((p) => !p)}>
          <Folder className="h-4 w-4" /> {t("code.picker.browse")}
        </Button>
        {/* Commands, for THIS project. The lever for this was global — one setting covering every
            folder — so the honest choices were "the agent may never run anything" and "it may run
            anything anywhere". Running the tests is what separates writing files from building
            something that works, and it is a decision people make per project.
            A button rather than a checkbox in a menu: it states what is true right now, and the
            state it is in is readable without opening anything. Only with a project — there is no
            "this project" to grant when none is chosen. */}
        {workspace ? (
          <Tooltip label={t(shellHere ? "code.shell.onHint" : "code.shell.offHint")}>
            <Button
              size="sm"
              type="button"
              variant={shellHere ? "outline" : "ghost"}
              aria-pressed={shellHere}
              disabled={grantShell.isPending}
              onClick={() => grantShell.mutate(!shellHere)}
            >
              <Terminal className="h-4 w-4" />
              {t(shellHere ? "code.shell.on" : "code.shell.off")}
            </Button>
          </Tooltip>
        ) : null}
        {/* The way out of a project, which did not exist.
            A conversation with no workspace works — the turn runs, answers, is priced and reads
            memory — and the sidebar already groups those under their own heading. What was missing
            was any way to START one: once a folder was chosen, every new conversation inherited it,
            and nothing on the screen let go. So the plain "ask it something" conversation was
            reachable only by never having opened a project in the first place.
            Only when there IS one to leave, and `switchProject("")` already does exactly the right
            thing — clears the root, drops the session id, opens a new conversation. */}
        {workspace ? (
          <Tooltip label={t("code.noProject.hint")}>
            <Button size="sm" type="button" variant="ghost" onClick={() => switchProject("")}>
              <MessageSquare className="h-4 w-4" /> {t("code.noProject")}
            </Button>
          </Tooltip>
        ) : null}
      </form>
      {picking ? (
        <div className="border-b border-hairline px-5 py-2">
          <ProjectPicker
            onCancel={() => setPicking(false)}
            onPick={(path) => {
              setPicking(false);
              switchProject(path);
            }}
          />
        </div>
      ) : null}
      {/* The folder's own pack, when it has one (study 29, P7.6): what it keeps and hides here, and
          whether it applies. Under the bar it qualifies rather than inside it, and absent for the
          many folders without one. */}
      {workspace ? <ProjectPackCard workspace={workspace} /> : null}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {showSessions && !viewerMax ? (
        <>
        {/* The width is the layout's (phase 2), dragged on the line beside it. */}
        <div
          className={cn("flex min-h-0 shrink-0 flex-col", sessionsEnter.className)}
          style={{ width: layout.regions.left.size ?? undefined }}
          onAnimationEnd={sessionsEnter.onAnimationEnd}
        >
        {/* The list takes what the panels moved here (phase 4) leave it. */}
        <div className="flex min-h-0 flex-1">
        <SessionSidebar
          workspace={workspace}
          activeSession={sessionId}
          onNew={() => {
            // A new conversation, not a cleared one: the old transcript stays on disk and stays in
            // the list. Clearing used to be the only way to start over, and it deleted the session.
            leaveConversation();
            setSessionId(null);
            writeLastSession(workspace, null);
            startConversation();
          }}
          onResume={resumeConversation}
          onProject={switchProject}
        />
        </div>
        <Dock zone="left" className="max-h-1/2 overflow-y-auto border-r border-hairline" />
        </div>
        {/* Only side by side: stacked below `lg`, there is no width between them to drag. */}
        <Splitter region="left" grows="right" className="hidden lg:block" />
        </>
        ) : (
          // Hidden, the list leaves a tab on the edge it was on. A running turn stays findable from
          // the status bar and, the moment the list is back, by its dot.
          <EdgeTab side="left" />
        )}
        {/* The conversation IS the screen. It used to be one of five panels in a 384px column, and
            the arithmetic did not work: the panels that could not shrink took every pixel and this
            was laid out at zero height. Git and the cost table now live on Work, where reviewing
            what a run did belongs; the two settings became one row inside the composer. */}
        {/* `min-w-0` on the column that is MEANT to absorb the shrinking.
            rc16 put it on the inner Conversation div and on the viewer, and missed this one — the
            row's own flex-1 child. So at 1280px the conversation held 778px of a 936px row, the
            viewer was crushed to 0.67px, and the pair overflowed 82px into the activity panel.
            Measured by hit-testing a grid inside the panel: the composer strip and the transcript
            bubbles were painting there, not the code this time. */}
        {/* Below `lg` the row is a column, and three stacked panes do not fit: the conversation
            needs ~368px for its composer and header alone, and capping all three to make room
            left it 161px and overflowing onto the viewer. So below `lg` the viewer REPLACES the
            conversation rather than sharing the height with it — which is what the note over
            the viewer already says it is: a consequence of opening a file, not a third of the
            window. Close the file and the conversation is back. */}
        {/* `ref` after `className`, on this line: columns-can-shrink.test.ts reads this line. */}
        <main className={cn("flex min-h-0 min-w-0 flex-1 flex-col", viewerFull && "max-lg:hidden", viewerMax && "hidden")} ref={transcriptRoot}>
          {findOpen ? (
            <FindBar
              scope={transcriptRoot}
              onClose={() => setFindOpen(false)}
              focusTick={findFocus}
              step={findStep}
            />
          ) : null}
          <Conversation
            key={conversationKey}
            resumeSession={sessionId}
            // Only over the default: a style the person picked while the conversation loaded is
            // theirs, and the stored one is a fact about the past turn, not a newer choice.
            onStyleRestored={(restored) => setStyle((current) => (current === "default" ? restored : current))}
            workspace={workspace}
            openFile={openFile}
            onOpenFile={showFile}
            // Two conversations at once: this one in a window of its own, the next one here.
            onOpenWindow={(id) =>
              window.open(conversationUrl(id), conversationWindowName(id), CONVERSATION_WINDOW_FEATURES)
            }
            onHandOff={(text) =>
              // Straight into the shared run — the multi-attempt, revert-if-it-fails path the button
              // actually promises. It used to fill a form below and wait, so the user could set the
              // verify command and the attempt count first; those fields no longer exist, and the
              // server infers the verify command from the project. Waiting for someone to press a
              // second button on a form with nothing left to fill in is not consent, it is friction.
              // The global status bar shows it and can stop it from any screen.
              run.start({
                task: text,
                verify: null,
                workspace: workspace || null,
                max_attempts: 3,
                // The bar above IS the picker now, and this dropped what it chose: a run started
                // here took the built-in tiers whatever the bar said, and then filed the receipt
                // under "system". `worth.py` groups by (profile, profile_source) precisely so a run
                // somebody chose "max" for and a run that got the default stay separate evidence —
                // which only works if what was chosen actually travels.
                profile,
                // `null` rather than four empty strings when nothing was picked: the server merges
                // field by field and reads a missing key as "keep the profile's answer", so a blank
                // slug would be a request to run that role on a model named empty-string.
                roles: toRoleModels(roles),
                profile_source: profileTouched ? "user" : "system",
              })
            }
            onBatch={(tasks) => setBatch({ tasks, at: Date.now() })}
            onEdited={refreshOpenFile}
            busyElsewhere={runBusy}
            runLive={runBusy}
            posture={posture}
            provider={provider}
            model={model}
            style={style}
            profile={profile}
            /* No selectors. What they expressed is now a server default the app SENDS (never omits
               — an absent posture means no tool denials and no pause at all, which is more permissive
               than any corner of the grid a user could have chosen) and a line of evidence in the
               transcript once it becomes relevant. */
            controls={
              <div className="flex flex-col gap-1.5">
                {/* Minimised, the three pickers are one line of chips (dynamic screen, phase 4). The
                    posture note below is outside it on purpose: the no-sandbox warning never hides. */}
                <ComposerSettings
                  summary={[
                    provider === "" ? t("code.provider.native") : provider,
                    ...(provider === "" ? [t(`code.roles.profile.${profile}`)] : []),
                    ...(provider === "" ? [model ? (model.split("/").pop() ?? model) : t("model.pick.default")] : []),
                    // The style only once one is picked: a chip for the default would be a line
                    // describing nothing, on the strip that exists to be glanced past.
                    ...(provider === "" && style !== "default" ? [styleLabel(t, style)] : []),
                  ]}
                >
                {/* The picker sits ABOVE the sentence, because the sentence is about the choice.
                    Reversed, someone reads what the agent may do to their files and then changes
                    who the agent is — which is the order in which a promise stops being true. */}
                <ProviderPicker value={provider} onChange={setProvider} disabled={runBusy} />
                {/* Only for Chimera's own loop, same as the model picker below: an external agent
                    routes its own roles, and offering the control beside it would describe a
                    decision this app does not get to make. */}
                {provider === "" ? (
                  <RolesBar
                    compact
                    profile={profile}
                    onProfile={(p) => {
                      setProfile(p);
                      setProfileTouched(true);
                    }}
                    override={roles}
                    onOverride={(o) => {
                      setRoles(o);
                      setProfileTouched(true);
                    }}
                    oneModel={oneModel}
                    onOneModel={setOneModel}
                    disabled={runBusy}
                  />
                ) : null}
                {/* Only for Chimera's own loop. Claude Code and Gemini pick their own model, so a
                    selector next to them would offer a choice this app cannot make — and the turn
                    would run on something other than what the row says. */}
                {provider === "" ? (
                  <ModelPicker value={model} onChange={setModel} disabled={runBusy} />
                ) : null}
                {/* Only for Chimera's own loop, like the model: an external agent's prompt is not ours
                    to suffix, and the server would not apply the style there. */}
                {provider === "" ? (
                  <StylePicker value={style} onChange={setStyle} disabled={runBusy} />
                ) : null}
                </ComposerSettings>
                <PostureNote
                  workspace={workspace}
                  reach={reach}
                  approval={approval}
                  provider={provider}
                />
              </div>
            }
          />
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
        {/* `min-w-0` for the same reason as the conversation column beside it: a fixed
            width is a BASIS, not a ceiling, and a flex child without it will not shrink —
            it overflowed the row instead and painted across the activity panel. */}
        {/* Closed, it stays mounted and hidden rather than unmounted: the toast's Undo and the tray's
            Show bring back the same file, with an unsaved draft still in it. */}
        {openFile ? (
          <>
          {viewerMax || viewerMin || viewerClosed ? null : <Splitter region="viewer" grows="left" className="hidden lg:block" />}
          {/* The width comes from the layout through a variable, because it applies only side by side:
              below `lg` the viewer REPLACES the conversation and takes the whole column. */}
          <div
            hidden={viewerClosed}
            // One line on purpose: `columns-can-shrink.test.ts` reads the roles off it. Maximised (phase 5), the
            // viewer drops its width and grows; otherwise it holds `--viewer-w` from `lg` up.
            className={cn("flex min-h-0 min-w-0 flex-1 flex-col border-hairline", viewerMax ? "lg:w-auto" : "lg:w-(--viewer-w) lg:flex-none lg:shrink-0 lg:border-l", viewerMin && "max-lg:flex-none max-lg:border-t lg:w-72 lg:self-start lg:border-b")}
            style={{ "--viewer-w": `${layout.regions.viewer.size ?? 448}px` } as CSSProperties}
          >
            <Viewer
              workspace={workspace}
              path={openFile}
              minimized={viewerMin}
              onMinimize={(min) => layoutDispatch({ type: "set-mode", panel: "viewer", mode: min ? "minimized" : "open" })}
              onClose={closeViewer}
            />
          </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
