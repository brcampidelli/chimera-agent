import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { TerminalSquare, X } from "lucide-react";

import { Editor } from "@/components/editor/Editor";
import { diagnosticsExtension } from "@/components/editor/diagnostics";
import { inlineCompletion } from "@/components/editor/inline";
import { Runner } from "@/components/editor/Runner";
import { getFsFile, saveFile } from "@/lib/api";
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
      <button type="button" onClick={onSelect} title={path} className={cn("py-1.5", focusRing)}>
        {baseName(path)}
      </button>
      {/* The dot is not decoration — it is the only thing on screen saying this file differs from
          disk, and it sits where the close button is so the two are read together. */}
      <button
        type="button"
        onClick={onClose}
        aria-label={t("edit.close", { file: baseName(path) })}
        className={cn(
          "flex h-5 w-5 items-center justify-center rounded",
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
  onOpen,
}: {
  workspace: string | null;
  /** The file to show, from the URL. Null means nothing is open. */
  path: string | null;
  /** Change the URL. The tree and the tab strip both go through this. */
  onOpen: (path: string | null) => void;
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

  // A path arriving from the URL (a link, a reload, the tree) joins the strip.
  useEffect(() => {
    if (!path) return;
    setOpen((prev) => (prev.includes(path) ? prev : [...prev, path]));
  }, [path]);

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
  });

  const dirty = path !== null && drafts[path] !== undefined;
  const disk = file.data?.content;
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
  const readOnly = truncated;

  const save = useMutation({
    mutationFn: async () => {
      if (path === null) return;
      const text = drafts[path];
      if (text === undefined) return;
      await saveFile(workspace, path, text);
    },
    onSuccess: () => {
      if (path === null) return;
      setDrafts((d) => forget(d, path));
      setBase((b) => forget(b, path));
      void qc.invalidateQueries({ queryKey: ["fs-file", workspace, path] });
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
  const saveRef = useRef(save.mutate);
  saveRef.current = save.mutate;

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
      // Closing the file you are looking at moves you to its left-hand neighbour, then its
      // right-hand one — where your eye already is. Jumping to the end of the strip instead is the
      // small wrongness that makes closing several tabs feel like being shoved around.
      if (target === path) onOpen(next[at - 1] ?? next[at] ?? null);
    },
    [open, path, onOpen],
  );

  return (
    <div className="flex h-full flex-col">
      {open.length > 0 && (
        <div className="flex shrink-0 overflow-x-auto border-b border-hairline bg-card/40">
          {open.map((p) => (
            <Tab
              key={p}
              path={p}
              active={p === path}
              dirty={drafts[p] !== undefined}
              onSelect={() => onOpen(p)}
              onClose={() => close(p)}
            />
          ))}
        </div>
      )}

      {path === null ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-1 text-center">
          <p className="text-sm text-foreground">{t("edit.empty.title")}</p>
          <p className="text-xs text-muted-foreground">{t("edit.empty.hint")}</p>
        </div>
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
                className={cn("rounded px-1.5 py-0.5 underline", focusRing)}
              >
                {t("edit.conflict.keep")}
              </button>
              <button
                type="button"
                onClick={() => {
                  setDrafts((d) => forget(d, path));
                  setBase((b) => forget(b, path));
                }}
                className={cn("rounded px-1.5 py-0.5 underline", focusRing)}
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
          <div className="min-h-0 flex-1">
            <Editor
              path={path}
              doc={drafts[path] ?? disk ?? ""}
              readOnly={readOnly}
              extra={extra}
              onChange={draft}
              onSave={() => saveRef.current()}
            />
          </div>
          {runner && (
            <div className="h-56 shrink-0">
              <Runner workspace={workspace} />
            </div>
          )}
          <div className="flex shrink-0 items-center justify-between border-t border-hairline px-3 py-1 text-xs text-muted-foreground">
            <span className="truncate" title={path}>
              {path}
            </span>
            <span className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setRunner((on) => !on)}
                aria-pressed={runner}
                className={cn(
                  "flex items-center gap-1 rounded px-1.5 py-0.5 hover:bg-surface-hover hover:text-foreground",
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
                  onClick={() => save.mutate()}
                  disabled={readOnly}
                  className={cn("rounded px-1.5 py-0.5 text-accent hover:bg-surface-hover", focusRing)}
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
