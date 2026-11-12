import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EyeOff, Pin, PinOff, RotateCcw } from "lucide-react";

import { SelectionBar, type BarAction } from "@/components/SelectionBar";
import { Button } from "@/components/ui/button";
import { Checkbox, triState } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { Tooltip } from "@/components/ui/tooltip";
import {
  flagCodeProject,
  grantCodeProjectShell,
  listCodeSessions,
  type CodeProject,
} from "@/lib/api";
import { runBulk } from "@/lib/bulk";
import { useT } from "@/lib/i18n";
import { loadProjects, projectLabel, aliasesOf, sidebarOrder } from "@/lib/projects";

/** Add or remove one path from a selection, as a new set. */
function withPath(prev: Set<string>, path: string, on: boolean): Set<string> {
  const next = new Set(prev);
  if (on) next.add(path);
  else next.delete(path);
  return next;
}

/**
 * The folders this install knows, and the three things decided about each: may the agent run
 * commands there, does it come first in the sidebar, and is it listed at all.
 *
 * The command switch is the SAME record the Code screen's "Commands" button writes and every turn is
 * held to on the server (`assemble_registry`). It used to live in that screen's browser storage,
 * which nothing outside the screen could see; here it can be read and revoked for every folder at
 * once, which is the point of putting it in Settings.
 *
 * Listed: every registered folder plus every folder a conversation was filed under — the same union
 * the sidebar shows, in the same order — so a folder you have worked in can be granted, pinned or
 * removed without first being "added".
 *
 * Select several, or all, and act on them at once: with dozens of folders, one click per folder per
 * action was the whole job. The bulk actions are the same client calls the row buttons make, one per
 * folder; there is no bulk route on the server to disagree with the single ones.
 *
 * Not here: a "show in the file explorer" button. The window grants the page no native calls
 * (`src-tauri/capabilities/default.json`), and the only thing the shell hands to the operating
 * system is an http(s) link (`on_new_window` in `main.rs`). Opening a local folder would need a new
 * native command, and that is a decision about the window's privileges, not a button.
 */
export function FoldersCard({ reach }: { reach: string }) {
  const t = useT();
  const headingId = useId();
  const qc = useQueryClient();
  const projects = useQuery({ queryKey: ["code-projects"], queryFn: loadProjects });
  // Wrapped, not passed by reference: the card renders inside Settings, and a client function
  // that is missing (an older server's mock in a test, a module that failed to load) must fail
  // THIS query, not the whole Settings screen during render.
  const sessions = useQuery({ queryKey: ["code-sessions"], queryFn: () => listCodeSessions() });
  const rows = projects.data ?? [];
  const byPath = new Map(rows.map((row) => [row.path, row] as const));
  const aliases = aliasesOf(rows);
  // The empty workspace is "the app's own folder", not a folder anyone chose.
  const named = (sessions.data ?? []).filter((s) => s.workspace);
  const shown = sidebarOrder(named, rows, "").filter(Boolean);
  const hidden = rows.filter((row) => row.hidden);

  const keep = { onSuccess: (next: CodeProject[]) => qc.setQueryData(["code-projects"], next) };
  const grant = useMutation({
    mutationFn: ({ path, on }: { path: string; on: boolean }) => grantCodeProjectShell(path, on),
    ...keep,
  });
  const flag = useMutation({
    mutationFn: ({ path, ...flags }: { path: string; pinned?: boolean; hidden?: boolean }) =>
      flagCodeProject(path, flags),
    ...keep,
  });

  // Selection. A path that stopped being listed (hidden, or gone) drops out of the count on its own:
  // the sets are only ever read through the lists as they are now.
  const [picked, setPicked] = useState<Set<string>>(() => new Set());
  const [pickedHidden, setPickedHidden] = useState<Set<string>>(() => new Set());
  const [run, setRun] = useState<{ done: number; total: number } | null>(null);
  const [failure, setFailure] = useState<{ failed: number; total: number } | null>(null);
  const [confirmGrant, setConfirmGrant] = useState<string[] | null>(null);
  const chosen = shown.filter((path) => picked.has(path));
  const chosenHidden = hidden.map((row) => row.path).filter((path) => pickedHidden.has(path));
  const running = run !== null;

  /** One call per path, in order — the registry is one file the server rewrites on each write — and
   *  the list the last successful call returned becomes the list on screen. A failure is counted and
   *  the rest still go; the failed ones stay selected so retrying is one click. */
  async function apply(
    paths: string[],
    call: (path: string) => Promise<CodeProject[]>,
    drop: (done: string[]) => void,
  ) {
    if (paths.length === 0 || running) return;
    setFailure(null);
    setRun({ done: 0, total: paths.length });
    const latest: { rows: CodeProject[] | null } = { rows: null };
    const result = await runBulk(
      paths,
      async (path) => {
        latest.rows = await call(path);
      },
      { onProgress: (done, total) => setRun({ done, total }) },
    );
    if (latest.rows) qc.setQueryData(["code-projects"], latest.rows);
    drop(result.ok);
    setRun(null);
    if (result.failed.length > 0) setFailure({ failed: result.failed.length, total: paths.length });
  }
  const dropShown = (done: string[]) =>
    setPicked((prev) => new Set([...prev].filter((path) => !done.includes(path))));
  const dropHidden = (done: string[]) =>
    setPickedHidden((prev) => new Set([...prev].filter((path) => !done.includes(path))));
  // Only the folders the action would change: pinning thirty folders of which ten are pinned is
  // twenty writes, and the counter should say twenty.
  const pinAll = (pinned: boolean) =>
    apply(
      chosen.filter((path) => (byPath.get(path)?.pinned === true) !== pinned),
      (path) => flagCodeProject(path, { pinned }),
      dropShown,
    );
  const grantAll = (on: boolean) =>
    apply(
      chosen.filter((path) => (byPath.get(path)?.shell_granted === true) !== on),
      (path) => grantCodeProjectShell(path, on),
      dropShown,
    );
  const hideAll = () => apply(chosen, (path) => flagCodeProject(path, { hidden: true }), dropShown);
  const restoreAll = () =>
    apply(chosenHidden, (path) => flagCodeProject(path, { hidden: false }), dropHidden);
  const barProps = (count: number) => ({
    label: t("bulk.selectedCount", { n: count }),
    busy: running,
    progress: run ? t("bulk.progress", { done: run.done, total: run.total }) : undefined,
    notice: failure ? t("bulk.failed", { failed: failure.failed, total: failure.total }) : undefined,
  });
  const foldersActions: BarAction[] = [
    { id: "pin", label: t("settings.folders.pin"), onClick: () => void pinAll(true) },
    { id: "unpin", label: t("settings.folders.unpin"), onClick: () => void pinAll(false) },
    {
      id: "grant",
      label: t("bulk.folders.grant"),
      // Widening what the agent may run is the one bulk action that asks first.
      onClick: () => {
        const todo = chosen.filter((path) => byPath.get(path)?.shell_granted !== true);
        if (todo.length > 0) setConfirmGrant(todo);
      },
    },
    { id: "revoke", label: t("bulk.folders.revoke"), onClick: () => void grantAll(false) },
    { id: "hide", label: t("settings.folders.hide"), onClick: () => void hideAll(), danger: true },
  ];

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.folders")}
      </h2>
      <div className="divide-y divide-hairline">
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.folders.intro")}</p>
        {/* Said, not hidden: under these two standing reaches the switches below decide nothing,
            and a switch that reads "on" over a run that cannot use it is the lie this card is for
            removing. The server agrees — the grant never beats read_only. */}
        {reach === "read_only" ? (
          <p className="px-4 py-2.5 text-xs text-warn-foreground">{t("settings.folders.readOnly")}</p>
        ) : null}
        {reach === "workspace_shell" ? (
          <p className="px-4 py-2.5 text-xs text-muted-foreground">
            {t("settings.folders.everywhere")}
          </p>
        ) : null}
        {shown.length === 0 ? (
          <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.folders.empty")}</p>
        ) : (
          <>
            <label className="flex items-center gap-3 px-4 py-2 text-xs text-muted-foreground">
              <Checkbox
                checked={triState(chosen.length, shown.length)}
                onChange={(on) => setPicked(on ? new Set(shown) : new Set())}
                label={t("bulk.selectAll")}
              />
              {t("bulk.selectAll")}
            </label>
            {chosen.length > 0 || failure ? (
              <SelectionBar
                {...barProps(chosen.length)}
                actions={foldersActions}
                onClear={() => {
                  setPicked(new Set());
                  setFailure(null);
                }}
              />
            ) : null}
            <ul className="divide-y divide-hairline">
              {shown.map((path) => {
                const row = byPath.get(path);
                const name = projectLabel(path, aliases);
                const pinned = row?.pinned === true;
                return (
                  <li key={path} className="flex items-center gap-3 px-4 py-2.5">
                    <Checkbox
                      checked={picked.has(path)}
                      onChange={(on) => setPicked((prev) => withPath(prev, path, on))}
                      label={t("bulk.pick", { name })}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm">{name}</div>
                      <div className="truncate font-mono text-xs text-muted-foreground" title={path}>
                        {path}
                      </div>
                      {row?.shell_granted && row.granted_at ? (
                        <div className="text-xs text-muted-foreground">
                          {t("settings.folders.grantedOn", { date: row.granted_at.slice(0, 10) })}
                        </div>
                      ) : null}
                    </div>
                    <Tooltip label={t(pinned ? "settings.folders.unpin" : "settings.folders.pin")}>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-pressed={pinned}
                        disabled={running}
                        aria-label={t(pinned ? "settings.folders.unpin" : "settings.folders.pin")}
                        onClick={() => flag.mutate({ path, pinned: !pinned })}
                      >
                        {pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
                      </Button>
                    </Tooltip>
                    <Tooltip label={t("settings.folders.hideHint")}>
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t("settings.folders.hide")}
                        disabled={running}
                        onClick={() => flag.mutate({ path, hidden: true })}
                      >
                        <EyeOff className="h-4 w-4" />
                      </Button>
                    </Tooltip>
                    <Switch
                      checked={row?.shell_granted === true}
                      label={t("settings.folders.commandsIn", { name })}
                      disabled={grant.isPending || running}
                      onChange={(on) => grant.mutate({ path, on })}
                    />
                  </li>
                );
              })}
            </ul>
          </>
        )}
        {hidden.length ? (
          <div className="px-4 py-2.5">
            <div className="mb-1 text-xs font-medium text-muted-foreground">
              {t("settings.folders.hidden")}
            </div>
            <label className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
              <Checkbox
                checked={triState(chosenHidden.length, hidden.length)}
                onChange={(on) =>
                  setPickedHidden(on ? new Set(hidden.map((row) => row.path)) : new Set())
                }
                label={t("bulk.selectAllHidden")}
              />
              {t("bulk.selectAllHidden")}
            </label>
            {chosenHidden.length > 0 ? (
              <div className="mb-1 overflow-hidden rounded-card">
                <SelectionBar
                  {...barProps(chosenHidden.length)}
                  compact
                  actions={[
                    { id: "restore", label: t("bulk.folders.restore"), onClick: () => void restoreAll() },
                  ]}
                  onClear={() => setPickedHidden(new Set())}
                />
              </div>
            ) : null}
            <ul className="space-y-1">
              {hidden.map((row) => (
                <li key={row.path} className="flex items-center gap-2">
                  <Checkbox
                    checked={pickedHidden.has(row.path)}
                    onChange={(on) => setPickedHidden((prev) => withPath(prev, row.path, on))}
                    label={t("bulk.pick", { name: row.path })}
                  />
                  <span className="min-w-0 flex-1 truncate font-mono text-xs" title={row.path}>
                    {row.path}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={running}
                    onClick={() => flag.mutate({ path: row.path, hidden: false })}
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> {t("settings.folders.restore")}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {grant.isError || flag.isError ? (
          <p className="px-4 py-2.5 text-xs text-bad-foreground">{t("settings.folders.saveError")}</p>
        ) : null}
      </div>
      <Dialog
        open={confirmGrant !== null}
        onOpenChange={(next) => !next && setConfirmGrant(null)}
        title={t("bulk.folders.grantTitle", { n: confirmGrant?.length ?? 0 })}
      >
        <p className="text-sm text-muted-foreground">{t("bulk.folders.grantBody")}</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmGrant(null)}>
            {t("common.cancel")}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              const todo = confirmGrant ?? [];
              setConfirmGrant(null);
              void apply(todo, (path) => grantCodeProjectShell(path, true), dropShown);
            }}
          >
            {t("bulk.folders.grant")}
          </Button>
        </div>
      </Dialog>
    </section>
  );
}
