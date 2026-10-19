import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, TerminalSquare, X } from "lucide-react";
import { EditorView } from "@codemirror/view";

import { Editor, type EditorCursor } from "@/components/editor/Editor";
import { Breadcrumb } from "@/components/editor/Breadcrumb";
import { EmptyEditor } from "@/components/editor/EmptyEditor";
import { QuickOpen } from "@/components/editor/QuickOpen";
import { HtmlPreview } from "@/components/code/HtmlPreview";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { AgentChangeBanner, ProposalPanel } from "@/components/editor/InlineDiff";
import { SelectionBar } from "@/components/editor/SelectionBar";
import { useInlineEdit } from "@/components/editor/useInlineEdit";
import { languageName } from "@/components/editor/languages";
import { diagnosticsExtension } from "@/components/editor/diagnostics";
import { inlineCompletion } from "@/components/editor/inline";
import { Runner } from "@/components/editor/Runner";
import { getFsFile, listRunningTurns, saveFile } from "@/lib/api";
import type { FsFile } from "@/lib/types";
import {
  linesOf,
  sameText,
  wholeLines,
  type LineSelection,
  type SelectionIntent,
} from "@/lib/editorSelection";
import { pushRecent } from "@/lib/editorRecent";
import { matchesShortcut } from "@/lib/hotkeys";
import { useRunSession } from "@/lib/run-session";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";
import { Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { useT } from "@/lib/i18n";

/**
 * The editor screen.
 *
 * Deliberately NOT the chat screen. `189e3c7` removed the file tree and the command runner from the
 * conversation with a written reason — that screen is for asking, and asking should not begin with
 * navigation. Adding an editor back into it would reverse that decision by accretion. So this is its
 * own address (`#/edit`), reachable from the rail and from a link, and the conversation stays a
 * conversation.
 *
 * What it owns: which files are open, which one you are looking at, and what you have typed but not
 * saved. The active file lives in the URL, which is what makes "open this file" something any part
 * of the app can do with a link instead of a callback.
 */

/** What the status bar says the file is encoded in. See the note where it is shown. */
const ENCODING = "UTF-8";

/** The last segment of a path — what a tab is called. */
export function baseName(path: string): string {
  return path.split(/[/\\]/).pop() || path;
}

/** A copy without `key`, or the same object when there was nothing to remove — so a no-op cannot
 *  cause a re-render. */
function forget<T>(map: Record<string, T>, key: string): Record<string, T> {
  if (map[key] === undefined) return map;
  const next = { ...map };
  delete next[key];
  return next;
}

function Tab({
  path,
  active,
  dirty,
  onSelect,
  onClose,
}: {
  path: string;
  active: boolean;
  dirty: boolean;
  onSelect: () => void;
  onClose: () => void;
}) {
  const t = useT();
  return (
    <div
      className={cn(
        "group flex shrink-0 items-center gap-1 border-r border-hairline pl-3 pr-1 text-sm",
        "transition-colors duration-1 ease-out",
        active
          ? "bg-surface-2 text-foreground"
          : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
      )}
    >
      {/* Named with its state: the dot beside it is the only sign this file differs from disk, and a
          dot has no name, so a screen reader announced an unsaved file exactly like a saved one. */}
      <button
        type="button"
        onClick={onSelect}
        title={path}
        aria-label={dirty ? t("edit.tabUnsaved", { file: baseName(path) }) : undefined}
        className={cn("py-1.5", focusRing)}
      >
        {baseName(path)}
      </button>
      {/* The dot is not decoration — it is the only thing on screen saying this file differs from
          disk, and it sits where the close button is so the two are read together. */}
      <button
        type="button"
        onClick={onClose}
        aria-label={t("edit.close", { file: baseName(path) })}
        className={cn(
          "flex h-5 w-5 items-center justify-center rounded-sm",
          "hover:bg-surface-hover hover:text-foreground",
          focusRing,
        )}
      >
        {dirty ? (
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-accent group-hover:hidden" />
        ) : null}
        <X className={cn("h-3 w-3", dirty && "hidden group-hover:block")} />
      </button>
    </div>
  );
}

export function Edit({
  workspace,
  path,
  line = null,
  onOpen,
  onAsk,
}: {
  workspace: string | null;
  /** The file to show, from the URL. Null means nothing is open. */
  path: string | null;
  /** The line to bring into view and put the cursor on, 1-based — a search hit's. From the URL too. */
  line?: number | null;
  /** Change the URL. The tree and the tab strip both go through this. */
  onOpen: (path: string | null) => void;
  /** "Ask Chimera" on a selection: send the question in this project's conversation (the app takes
   *  it to the Code screen). Absent, the bar offers only "Change this", which stays here. */
  onAsk?: (request: { message: string; selection: LineSelection }) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string[]>([]);
  // Typed-but-unsaved text, by path. A Map and not per-file state because the editor keeps its own
  // document; this is only what has to survive switching tabs and what a save sends.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // What each dirty file said on disk when its draft began. The only way to tell "the agent changed
  // this file" from "this file was always like that".
  const [base, setBase] = useState<Record<string, string>>({});
  // What the editor last showed of each file with no draft: the disk as it was when the file was
  // opened, saved, or a change to it was kept. A disk that moves away from this while the file is
  // open and unedited is HELD — shown as a diff to keep or undo — instead of swapped in silently,
  // which is how an agent's edit used to land in front of someone mid-read with no sign it had.
  const [shown, setShown] = useState<Record<string, string>>({});
  const [cursor, setCursor] = useState<EditorCursor | null>(null);
  const [barMode, setBarMode] = useState<SelectionIntent | null>(null);
  const inline = useInlineEdit(workspace);
  // Whether an agent is working in this project now: a conversation's turn (any of them, from the
  // same list the status bar reads) or a verified run. While one is, the open file is re-read every
  // two seconds, so its change shows up as it lands rather than on the next window focus.
  const turns = useQuery({ queryKey: ["code-turns-running"], queryFn: listRunningTurns, refetchInterval: 4000 });
  const run = useRunSession(workspace);
  const agentActive =
    (turns.data ?? []).some((turn) => !workspace || turn.workspace === workspace) ||
    (run.running && (run.workspace === null || run.workspace === workspace));
  // Sticky until the file is back in step: a turn that finished a second before the poll still
  // wrote what the banner is about to show.
  const agentSeen = useRef(false);
  if (agentActive) agentSeen.current = true;

  // Ctrl+P. A capture listener on the window: CodeMirror binds the key itself on some platforms, and
  // the WebView's own meaning for it (print) must not win either.
  const [quickOpen, setQuickOpen] = useState(false);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!matchesShortcut(e, "editorQuickOpen")) return;
      e.preventDefault();
      e.stopPropagation();
      setQuickOpen(true);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
  // Files showing their rendered page instead of their source, by path (`.html` only).
  const [previewing, setPreviewing] = useState<Record<string, boolean>>({});
  // A tab whose close is waiting on an answer because it has unsaved typing.
  const [closing, setClosing] = useState<string | null>(null);
  const [closingFailed, setClosingFailed] = useState(false);

  // The tree's git marks and the empty state's "changed" list come from the git status; an agent
  // turn finishing is when they go stale without a save of ours to tell us.
  const wasAgentActive = useRef(false);
  useEffect(() => {
    if (wasAgentActive.current && !agentActive) {
      void qc.invalidateQueries({ queryKey: ["git-status"] });
      void qc.invalidateQueries({ queryKey: ["fs-tree"] });
    }
    wasAgentActive.current = agentActive;
  }, [agentActive, qc]);

  // A path arriving from the URL (a link, a reload, the tree) joins the strip.
  useEffect(() => {
    if (!path) return;
    setOpen((prev) => (prev.includes(path) ? prev : [...prev, path]));
    pushRecent(workspace, path);
  }, [path, workspace]);

  const file = useQuery({
    queryKey: ["fs-file", workspace, path],
    queryFn: () => getFsFile(workspace, path as string),
    enabled: path !== null,
    // The file on disk is not ours alone — the agent edits it too, which is the entire premise of
    // this app. The app-wide default is `false` (see main.tsx); here it is on, and `staleTime: 0`
    // makes it actually refetch, because coming back to the window is exactly the moment a run
    // has finished writing.
    refetchOnWindowFocus: true,
    staleTime: 0,
    refetchInterval: agentActive ? 2000 : false,
  });

  const dirty = path !== null && drafts[path] !== undefined;
  const disk = file.data?.content;
  // The first read of a file is what it says; every later one is compared with what is on screen.
  useEffect(() => {
    if (path === null || disk === undefined) return;
    setShown((s) => (s[path] !== undefined ? s : { ...s, [path]: disk }));
  }, [path, disk]);
  const held =
    path !== null && !dirty && disk !== undefined && shown[path] !== undefined && !sameText(shown[path], disk);
  /** Put `text` on screen as the file's settled content, and in the cache as what the disk says —
   *  written ourselves, so the refetch that follows has nothing to report. */
  const settle = (target: string, text: string) => {
    setShown((s) => ({ ...s, [target]: text }));
    qc.setQueryData<FsFile>(["fs-file", workspace, target], (old) => (old ? { ...old, content: text } : old));
    agentSeen.current = false;
  };

  // A search hit opens its file AT its line. The search panel always had the line and the sidebar
  // dropped it, so a hit on line 900 opened the file at line 1 and left the person scrolling for
  // what they had just clicked. Applied once per (path, line): the cursor is the person's after that,
  // and a refetch of the file must not yank it back.
  const viewRef = useRef<EditorView | null>(null);
  const jumped = useRef<string | null>(null);
  useEffect(() => {
    const v = viewRef.current;
    if (!v || path === null || line === null || disk === undefined) return;
    const key = `${path}:${line}`;
    if (jumped.current === key) return;
    jumped.current = key;
    // The child's effect has installed this file's document by now (children's effects run first).
    const target = v.state.doc.line(Math.min(Math.max(1, line), v.state.doc.lines));
    v.dispatch({ selection: { anchor: target.from }, effects: EditorView.scrollIntoView(target.from, { y: "center" }) });
  }, [path, line, disk]);
  /**
   * The file changed underneath an unsaved edit.
   *
   * `base` is what the file said when you started typing. If the disk no longer says that and you
   * have a draft, two people edited the same file — you and the agent — and one of you is about to
   * lose. The draft keeps winning on screen, because throwing away typing without asking is the
   * worse failure; but silence here would mean saving quietly reverts a run's work.
   *
   * This is a WARNING, not a lock. `PUT /api/fs/file` has no compare-and-swap, so nothing can
   * actually prevent the overwrite — claiming otherwise in the interface would be the dishonest
   * version of this feature.
   */
  const conflicted =
    path !== null && dirty && disk !== undefined && base[path] !== undefined && base[path] !== disk;
  // A truncated read is a PREFIX of the file. Saving it would delete everything past the cut, so
  // the editor refuses rather than trusting anyone to notice a warning.
  const truncated = file.data?.truncated === true;
  // A document (.docx/.pdf/.xlsx/.pptx) arrives as MarkItDown's TEXT of it, and a file with a note
  // ("binary or non-text") as an empty string. Saving either writes text over the real file, so
  // both are read-only here as they are in the Code viewer — and the server refuses the save too.
  const unwritable = truncated || !!file.data?.document || !!file.data?.note;
  // Held, too: typing over a change nobody has looked at yet would make the save the review.
  const readOnly = unwritable || held;

  const save = useMutation({
    // `text` is an accepted proposal; without it, the draft. Every write the Editor makes goes
    // through here — Save, Ctrl+S, Accept and Undo — so there is one write path to trust.
    mutationFn: async (text?: string) => {
      // Ctrl+S reaches here from the editor's keymap even when the button is disabled.
      if (path === null || unwritable) return null;
      const body = typeof text === "string" ? text : drafts[path];
      if (body === undefined) return null;
      await saveFile(workspace, path, body);
      return { path, body };
    },
    onSuccess: (written) => {
      if (!written) return;
      setDrafts((d) => forget(d, written.path));
      setBase((b) => forget(b, written.path));
      settle(written.path, written.body);
      void qc.invalidateQueries({ queryKey: ["fs-file", workspace, written.path] });
      // A save is what makes a file "modified" in git, so the marks are re-read.
      void qc.invalidateQueries({ queryKey: ["git-status"] });
    },
  });

  /** Record a keystroke. Also remembers what the file said when the draft began, which is what
   *  makes an outside change detectable at all. */
  const draft = (text: string) => {
    if (path === null) return;
    if (text === disk) {
      // Typed back to what is on disk. Not a change, so not dirty and not a conflict either.
      setDrafts((d) => forget(d, path));
      setBase((b) => forget(b, path));
      return;
    }
    setDrafts((d) => (d[path] === text ? d : { ...d, [path]: text }));
    setBase((b) => (b[path] !== undefined || disk === undefined ? b : { ...b, [path]: disk }));
  };

  // The save keybinding fires from inside CodeMirror, whose extensions are built once; this ref is
  // what lets that fixed keymap reach the current mutation and the current draft.
  const saveRef = useRef(() => save.mutate(undefined));
  saveRef.current = () => save.mutate(undefined);

  /**
   * Diagnostics, from `ruff server` — the same rules the CI job enforces.
   *
   * Built ONCE per workspace and shared by every tab, because a linter rebuilt on each render
   * restarts its own debounce on each keystroke and therefore never fires. It reads the current file
   * through a ref for the same reason.
   *
   * `lspNote` is the honest half. An editor with no squiggles on a machine without ruff would be
   * reporting a clean bill of health that nobody checked, so "nothing looked" gets its own line.
   */
  const [lspNote, setLspNote] = useState("");
  // The runner is off until asked for. It is a side panel on a screen whose subject is a file, and
  // opening one by default would spend a third of the height on a thing most sessions never use.
  const [runner, setRunner] = useState(false);
  const [completionNote, setCompletionNote] = useState("");
  const pathRef = useRef(path);
  pathRef.current = path;
  const extra = useMemo(
    () => [
      diagnosticsExtension(workspace, () => pathRef.current ?? "", setLspNote),
      // The single-flight key is the file, so switching tabs mid-request abandons the old one
      // rather than letting two files compete for the same local model.
      inlineCompletion(() => pathRef.current ?? "default", setCompletionNote),
    ],
    [workspace],
  );

  // The selection, in whole lines — what both "Ask Chimera" and "Change this" act on.
  const view = viewRef.current;
  const selection =
    path !== null && view !== null && cursor !== null && cursor.from !== cursor.to
      ? wholeLines(view.state.doc.toString(), cursor.from, cursor.to, path)
      : null;
  const proposal = inline.edit && path !== null && inline.edit.selection.path === path ? inline.edit : null;
  // The lines moved or changed since the proposal was asked for: applying it now would replace text
  // the agent never saw, so Accept is withdrawn and the panel says why.
  const stale =
    proposal !== null &&
    view !== null &&
    !sameText(linesOf(view.state.doc.toString(), proposal.selection.startLine, proposal.selection.endLine), proposal.selection.text);
  const canChange = !unwritable && !held;
  const canAsk = onAsk !== undefined;
  /** ⌘L / ⌘I from inside the editor: open the bar on that step, or let CodeMirror have the key. */
  const onSelectionKey = (intent: SelectionIntent): boolean => {
    if (!selection || inline.edit || dirty || (intent === "change" ? !canChange : !canAsk)) return false;
    setBarMode(intent);
    return true;
  };
  const submitSelection = (intent: SelectionIntent, instruction: string) => {
    if (!selection) return;
    setBarMode(null);
    if (intent === "ask") onAsk?.({ message: instruction, selection });
    else inline.start(selection, instruction);
  };
  /** Apply the proposal: into the editor as one undoable step, then through the Editor's own save.
   *  The only path by which a proposal reaches the file. */
  const acceptProposal = () => {
    if (!proposal || proposal.state.status !== "ready" || !view || stale || path === null) return;
    const { startLine, endLine } = proposal.selection;
    const from = view.state.doc.line(startLine).from;
    const to = view.state.doc.line(endLine).to;
    view.dispatch({ changes: { from, to, insert: proposal.state.replacement } });
    save.mutate(view.state.doc.toString());
    inline.clear();
  };

  const close = useCallback(
    (target: string) => {
      // Computed OUTSIDE the state updater. Navigating from inside one calls the parent's setState
      // during render, which React warns about and which is a real hazard: updaters may run twice
      // (StrictMode) or be discarded, so a side effect in there happens the wrong number of times.
      const at = open.indexOf(target);
      const next = open.filter((p) => p !== target);
      setOpen(next);
      setDrafts((d) => forget(d, target));
      setBase((b) => forget(b, target));
      // Reopened later, the file is read afresh: what it said while it was closed is not a change
      // anyone needs to review here.
      setShown((v) => forget(v, target));
      setPreviewing((v) => forget(v, target));
      // Closing the file you are looking at moves you to its left-hand neighbour, then its
      // right-hand one — where your eye already is. Jumping to the end of the strip instead is the
      // small wrongness that makes closing several tabs feel like being shoved around.
      if (target === path) onOpen(next[at - 1] ?? next[at] ?? null);
    },
    [open, path, onOpen],
  );

  const isHtml = path !== null && /\.html?$/i.test(path);
  const showPreview = path !== null && isHtml && previewing[path] === true && !file.data?.note;

  const saveAndClose = async (target: string) => {
    const body = drafts[target];
    if (body !== undefined) {
      try {
        await saveFile(workspace, target, body);
      } catch {
        // Nothing was written, so nothing is closed: the typing is still the only copy.
        setClosingFailed(true);
        return;
      }
      void qc.invalidateQueries({ queryKey: ["git-status"] });
    }
    setClosing(null);
    close(target);
  };

  return (
    <div className="flex h-full flex-col">
      <QuickOpen
        open={quickOpen}
        workspace={workspace}
        onClose={() => setQuickOpen(false)}
        onOpen={(target) => onOpen(target)}
      />
      <Dialog
        open={closing !== null}
        onOpenChange={(next) => !next && setClosing(null)}
        title={t("edit.closeUnsaved.title", { file: closing ? baseName(closing) : "" })}
        description={t("edit.closeUnsaved.body")}
        footer={
          <>
            <Button variant="ghost" onClick={() => setClosing(null)}>
              {t("edit.closeUnsaved.cancel")}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                if (closing) close(closing);
                setClosing(null);
              }}
            >
              {t("edit.closeUnsaved.discard")}
            </Button>
            <Button onClick={() => closing && void saveAndClose(closing)}>{t("edit.closeUnsaved.save")}</Button>
          </>
        }
      >
        {closingFailed && <p className="text-sm text-bad-foreground">{t("edit.saveFailed")}</p>}
      </Dialog>
      {open.length > 0 && (
        <div className="flex shrink-0 overflow-x-auto border-b border-hairline bg-card/40">
          {open.map((p) => (
            <Tab
              key={p}
              path={p}
              active={p === path}
              dirty={drafts[p] !== undefined}
              onSelect={() => onOpen(p)}
              onClose={() => {
                // Typing that exists nowhere else is not thrown away on a stray click.
                if (drafts[p] !== undefined) {
                  setClosingFailed(false);
                  setClosing(p);
                } else close(p);
              }}
            />
          ))}
        </div>
      )}

      {path !== null && <Breadcrumb workspace={workspace} path={path} />}
      {path === null ? (
        <EmptyEditor workspace={workspace} onOpen={onOpen} onQuickOpen={() => setQuickOpen(true)} />
      ) : file.isLoading ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : file.isError ? (
        <div className="flex flex-1 items-center justify-center">
          <ErrorState error={file.error} onRetry={() => file.refetch()} />
        </div>
      ) : (
        <>
          {/* The server's own note — "binary file", "too large" — in its own words. */}
          {file.data?.note && (
            <p className="shrink-0 border-b border-hairline px-3 py-1.5 text-xs text-muted-foreground">
              {file.data.note}
            </p>
          )}
          {truncated && (
            <p className="shrink-0 border-b border-hairline bg-warn/10 px-3 py-1.5 text-xs text-warn-foreground">
              {t("edit.truncated")}
            </p>
          )}
          {conflicted && (
            <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-hairline bg-warn/10 px-3 py-1.5 text-xs text-warn-foreground">
              <span>{t("edit.conflict")}</span>
              <button
                type="button"
                onClick={() => setBase((b) => ({ ...b, [path]: disk as string }))}
                className={cn("rounded-sm px-1.5 py-0.5 underline", focusRing)}
              >
                {t("edit.conflict.keep")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setDrafts((d) => forget(d, path));
                  setBase((b) => forget(b, path));
                  if (disk !== undefined) settle(path, disk);
                }}
                className={cn("rounded-sm px-1.5 py-0.5 underline", focusRing)}
              >
                {t("edit.conflict.reload")}
              </button>
            </div>
          )}
          {lspNote && (
            <p className="shrink-0 border-b border-hairline px-3 py-1.5 text-xs text-muted-foreground">
              {t("edit.lsp.unavailable", { note: lspNote })}
            </p>
          )}
          {/* Only a STANDING problem reaches here — no model server, model not pulled. An empty
              suggestion and a superseded request are silent, because unlike a missing diagnostic a
              missing suggestion makes no claim about the file. */}
          {completionNote && (
            <p className="shrink-0 border-b border-hairline px-3 py-1.5 text-xs text-muted-foreground">
              {t("edit.complete.unavailable", { note: completionNote })}
            </p>
          )}
          {held && (
            <AgentChangeBanner
              before={shown[path]}
              after={disk as string}
              byChimera={agentActive || agentSeen.current}
              onKeep={() => settle(path, disk as string)}
              // Undo writes back what was on screen, through the same save as everything else.
              onUndo={() => save.mutate(shown[path])}
              undoing={save.isPending}
            />
          )}
          {proposal && (
            <ProposalPanel
              startLine={proposal.selection.startLine}
              endLine={proposal.selection.endLine}
              instruction={proposal.instruction}
              original={proposal.selection.text}
              state={proposal.state}
              stale={stale}
              onAccept={acceptProposal}
              onReject={inline.clear}
              onCancel={inline.cancel}
            />
          )}
          {isHtml && !showPreview && !file.data?.note && (
            <div className="flex shrink-0 items-center border-b border-hairline px-3 py-1">
              <button
                type="button"
                onClick={() => setPreviewing((v) => ({ ...v, [path]: true }))}
                className={cn(
                  "flex items-center gap-1.5 rounded-chip px-2 py-1 text-xs text-muted-foreground",
                  "transition-colors duration-1 ease-out hover:text-foreground",
                  focusRing,
                )}
              >
                <Eye className="h-3.5 w-3.5" />
                {t("edit.preview.show")}
              </button>
            </div>
          )}
          {/* The page the file describes, drawn by the Code screen's own preview: the same sandbox
              (scripts only, an opaque origin, local stylesheets inlined), not a wider one. It shows
              what the editor holds, unsaved typing included. The editor stays mounted underneath so
              its cursor and undo history are still there when the source comes back. */}
          {showPreview && (
            <HtmlPreview
              key={path}
              workspace={workspace ?? ""}
              path={path}
              source={drafts[path] ?? disk ?? ""}
              onShowSource={() => setPreviewing((v) => forget(v, path))}
            />
          )}
          <div className={cn("relative min-h-0 flex-1", showPreview && "hidden")}>
            {selection && !proposal && !held && (canAsk || canChange) && (
              <SelectionBar
                startLine={selection.startLine}
                endLine={selection.endLine}
                mode={barMode}
                onMode={(mode) => {
                  setBarMode(mode);
                  if (mode === null) viewRef.current?.focus();
                }}
                canAsk={canAsk}
                canChange={canChange}
                // The server reads the lines from disk: unsaved, it would answer about other text.
                needsSave={dirty}
                onSubmit={submitSelection}
              />
            )}
            <Editor
              path={path}
              doc={drafts[path] ?? (held ? shown[path] : disk) ?? ""}
              readOnly={readOnly}
              extra={extra}
              onChange={draft}
              onSave={() => saveRef.current()}
              onReady={(v) => {
                viewRef.current = v;
              }}
              onCursor={(next) => {
                setCursor(next);
                // A bar opened on one selection does not carry over to the next.
                if (next.from === next.to) setBarMode(null);
              }}
              onSelectionKey={onSelectionKey}
            />
          </div>
          {runner && (
            <div className="h-56 shrink-0">
              <Runner workspace={workspace} />
            </div>
          )}
          <div
            className="flex shrink-0 items-center justify-between border-t border-hairline px-3 py-1 text-xs text-muted-foreground"
            data-testid="edit-status"
          >
            <span className="flex min-w-0 items-center gap-3">
              {cursor && (
                <span>{t("edit.status.position", { line: String(cursor.line), col: String(cursor.col) })}</span>
              )}
              <span>{languageName(path) ?? t("edit.status.plain")}</span>
              {/* Not detected: `fs_api.read_file` decodes strictly as UTF-8, and a file that is not
                  arrives with a note and is read-only. Anything editable here IS UTF-8. */}
              <span>{ENCODING}</span>
            </span>
            <span className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setRunner((on) => !on)}
                aria-pressed={runner}
                className={cn(
                  "flex items-center gap-1 rounded-sm px-1.5 py-0.5 hover:bg-surface-hover hover:text-foreground",
                  runner && "text-foreground",
                  focusRing,
                )}
              >
                <TerminalSquare className="h-3 w-3" />
                {t("runner.title")}
              </button>
              {readOnly && <span>{t("edit.readOnly")}</span>}
              {save.isError && <span className="text-bad">{t("edit.saveFailed")}</span>}
              {save.isPending ? (
                <span>{t("edit.saving")}</span>
              ) : dirty ? (
                <button
                  type="button"
                  onClick={() => save.mutate(undefined)}
                  disabled={readOnly}
                  className={cn("rounded-sm px-1.5 py-0.5 text-accent hover:bg-surface-hover", focusRing)}
                >
                  {t("edit.save")}
                </button>
              ) : (
                <span>{t("edit.saved")}</span>
              )}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
