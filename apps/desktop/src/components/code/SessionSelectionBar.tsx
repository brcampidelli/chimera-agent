import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { SelectionBar, type BarAction } from "@/components/SelectionBar";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import type { SessionSelection } from "@/components/code/useSessionSelection";
import {
  archiveCodeSession,
  deleteCodeSession,
  flagCodeProject,
  unarchiveCodeSession,
  type CodeProject,
  type CodeSessionMeta,
} from "@/lib/api";
import { runBulk } from "@/lib/bulk";
import { useT } from "@/lib/i18n";
import { forgetLineComments } from "@/lib/line-comments";
import type { SidebarGroup } from "@/lib/sidebar-groups";
import { readLastSession, writeLastSession } from "@/lib/workspace";

/** Conversations are separate files, so a few requests may overlap; the folder registry is one file
 *  and its writes go one at a time (`runBulk` default). */
const SESSION_CONCURRENCY = 4;

/**
 * "Select all" and the actions for what the sidebar has ticked: archive, bring back, delete, and
 * take whole folders off the lists.
 *
 * Every action is the single-row call run once per item, with the single row's own rules. Archive is
 * not offered for a conversation that is working or waiting (the server answers 409 and the row
 * hides the button), so those are left out and counted rather than sent to fail. Delete IS offered
 * for such a row, so it stays allowed here — but it asks first, in one dialog that says how many, and
 * says how many of them are still working.
 */
export function SessionSelectionBar({
  selection,
  active,
  archived,
  groups,
  busyIds,
}: {
  selection: SessionSelection;
  /** The conversations the list shows after the filter and search. */
  active: readonly CodeSessionMeta[];
  /** The archived ones, when that section is open. */
  archived: readonly CodeSessionMeta[];
  groups: readonly SidebarGroup[];
  /** Ids of conversations that are running or waiting on an answer. */
  busyIds: ReadonlySet<string>;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [run, setRun] = useState<{ done: number; total: number } | null>(null);
  const [notice, setNotice] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);

  const everyId = [...active, ...archived].map((s) => s.id);
  const chosen = active.filter((s) => selection.has(s.id));
  const chosenArchived = archived.filter((s) => selection.has(s.id));
  const chosenFolders = groups.filter(
    (g) =>
      g.key !== "" &&
      (g.sessions.length > 0 ? g.sessions.every((s) => selection.has(s.id)) : selection.folders.has(g.key)),
  );
  const emptyPicked = chosenFolders.filter((g) => g.sessions.length === 0).length;
  const count = chosen.length + chosenArchived.length + emptyPicked;
  const running = run !== null;

  /** Run one call per item, keep going past failures, say what happened, and untick what went. */
  async function apply<T>(
    items: readonly T[],
    call: (item: T) => Promise<unknown>,
    untick: (done: T[]) => void,
    options: { concurrency?: number; skipped?: number } = {},
  ) {
    if (running) return;
    setNotice("");
    setRun({ done: 0, total: items.length });
    const result = await runBulk(items, call, {
      concurrency: options.concurrency,
      onProgress: (done, total) => setRun({ done, total }),
    });
    untick(result.ok);
    setRun(null);
    void qc.invalidateQueries({ queryKey: ["code-sessions"] });
    const parts: string[] = [];
    if (result.failed.length > 0)
      parts.push(t("bulk.failed", { failed: result.failed.length, total: items.length }));
    if ((options.skipped ?? 0) > 0) parts.push(t("bulk.skippedBusy", { n: options.skipped ?? 0 }));
    setNotice(parts.join(" "));
  }

  const untickSessions = (done: CodeSessionMeta[]) =>
    selection.setMany(done.map((s) => s.id), false);

  function archiveChosen() {
    const idle = chosen.filter((s) => !busyIds.has(s.id));
    void apply(idle, (s) => archiveCodeSession(s.id), untickSessions, {
      concurrency: SESSION_CONCURRENCY,
      skipped: chosen.length - idle.length,
    });
  }

  function restoreChosen() {
    void apply(chosenArchived, (s) => unarchiveCodeSession(s.id), untickSessions, {
      concurrency: SESSION_CONCURRENCY,
    });
  }

  function deleteChosen() {
    setConfirmDelete(false);
    void apply(
      chosen,
      async (s) => {
        await deleteCodeSession(s.id);
        // The same housekeeping as deleting one: its unsent diff comments had nowhere left to go,
        // and it must not be the conversation its folder reopens.
        forgetLineComments(s.id);
        if (readLastSession(s.workspace) === s.id) writeLastSession(s.workspace, null);
      },
      untickSessions,
      { concurrency: SESSION_CONCURRENCY },
    );
  }

  function hideChosenFolders() {
    void apply(
      chosenFolders,
      async (g) => {
        const next: CodeProject[] = await flagCodeProject(g.key, { hidden: true });
        qc.setQueryData(["code-projects"], next);
      },
      (done) => {
        for (const g of done) selection.setFolder(g.key, false);
        selection.setMany(done.flatMap((g) => g.sessions.map((s) => s.id)), false);
      },
    );
  }

  const actions: BarAction[] = [];
  if (chosen.length > 0) {
    actions.push({ id: "archive", label: t("bulk.archive"), onClick: archiveChosen });
  }
  if (chosenArchived.length > 0) {
    actions.push({ id: "unarchive", label: t("bulk.unarchive"), onClick: restoreChosen });
  }
  if (chosen.length > 0) {
    actions.push({ id: "delete", label: t("bulk.delete"), onClick: () => setConfirmDelete(true), danger: true });
  }
  if (chosenFolders.length > 0) {
    actions.push({ id: "hide", label: t("bulk.hideFolders"), onClick: hideChosenFolders });
  }
  const busyAmongChosen = chosen.filter((s) => busyIds.has(s.id)).length;

  return (
    <>
      <label className="flex items-center gap-2 px-3 pb-1 text-xs text-muted-foreground">
        <Checkbox
          checked={selection.stateOf(everyId)}
          onChange={(on) => selection.setMany(everyId, on)}
          label={t("bulk.selectAll")}
        />
        {t("bulk.selectAll")}
      </label>
      {count > 0 || notice ? (
        <SelectionBar
          compact
          label={t("bulk.selectedCount", { n: count })}
          actions={actions}
          busy={running}
          progress={run ? t("bulk.progress", { done: run.done, total: run.total }) : undefined}
          notice={notice || undefined}
          onClear={() => {
            selection.clear();
            setNotice("");
          }}
        />
      ) : null}
      <Dialog
        open={confirmDelete}
        onOpenChange={(next) => !next && setConfirmDelete(false)}
        title={t("bulk.deleteTitle", { n: chosen.length })}
      >
        <p className="text-sm text-muted-foreground">{t("bulk.deleteBody")}</p>
        {busyAmongChosen > 0 ? (
          <p className="mt-2 text-sm text-warn-foreground">{t("bulk.deleteBusy", { n: busyAmongChosen })}</p>
        ) : null}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmDelete(false)}>
            {t("common.cancel")}
          </Button>
          <Button size="sm" onClick={deleteChosen}>
            {t("common.delete")}
          </Button>
        </div>
      </Dialog>
    </>
  );
}
