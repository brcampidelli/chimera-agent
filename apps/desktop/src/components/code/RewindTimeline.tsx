import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { History, Undo2 } from "lucide-react";

import {
  listCodeUndoPoints,
  previewCodeUndoPoint,
  redoCodeUndo,
  restoreCodeUndoPoint,
  type CodeUndoPoint,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";

/** The confirmation for going back to before one turn: what will change, and what will not.
 *
 *  The list comes from the server's own preview, which applies the rule the restore applies — a file
 *  edited since its turn is left alone — so the warning names exactly the files the restore will
 *  skip rather than a generic caution. Nothing is written until the button is pressed.
 */
function RewindDialog({
  sessionId,
  point,
  onClose,
  onDone,
}: {
  sessionId: string;
  point: CodeUndoPoint;
  onClose: () => void;
  onDone: (notice: string) => void;
}) {
  const t = useT();
  const [saving, setSaving] = useState(false);
  const preview = useQuery({
    queryKey: ["code-undo-preview", sessionId, point.turn_id],
    queryFn: () => previewCodeUndoPoint(sessionId, point.turn_id),
    staleTime: 0,
    gcTime: 0,
  });
  const files = preview.data?.files ?? point.files;
  const conflicts = preview.data?.conflicts ?? [];
  const ready = preview.data?.ok === true;

  async function confirm() {
    setSaving(true);
    try {
      const result = await restoreCodeUndoPoint(sessionId, point.turn_id);
      const left = [...(result.kept ?? []), ...(result.left_new_files ?? [])];
      onDone(
        !result.ok
          ? (result.reason ?? t("code.undo.unavailable"))
          : left.length
            ? t("code.undo.preserved", { files: left.join(", ") })
            : t("code.undo.restored", { n: result.restored }),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !saving) onClose();
      }}
      title={t("code.undo.confirmTitle")}
      description={t("code.undo.confirmDescription")}
      footer={
        <div className="flex justify-end gap-2">
          <Button variant="ghost" disabled={saving} onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button disabled={!ready || saving} data-testid="undo-confirm" onClick={() => void confirm()}>
            {t("code.undo.confirm")}
          </Button>
        </div>
      }
    >
      {preview.isLoading ? <p className="text-sm text-muted-foreground">{t("common.loading")}</p> : null}
      {preview.isError || preview.data?.ok === false ? (
        <p role="alert" className="text-sm text-warn-foreground">
          {preview.data?.reason ?? t("code.undo.loadFailed")}
        </p>
      ) : null}
      {ready ? (
        <>
          <p className="text-sm text-muted-foreground">{t("code.undo.preview", { n: files.length })}</p>
          <ul className="mt-2 max-h-48 overflow-auto rounded-lg bg-surface-2 p-3 text-xs">
            {files.map((file) => (
              <li key={file} className="break-all">
                {file}
              </li>
            ))}
          </ul>
          {conflicts.length > 0 ? (
            <p role="alert" data-testid="undo-conflicts" className="mt-3 text-xs text-warn-foreground">
              {t("code.undo.conflicts", { files: conflicts.join(", ") })}
            </p>
          ) : null}
          <p className="mt-3 text-xs text-muted-foreground">{t("code.undo.preservationWarning")}</p>
        </>
      ) : null}
    </Dialog>
  );
}

/** The conversation's rewind timeline: one entry per retained turn that changed files, each a way
 *  back to before that turn. A restore is one step that can itself be redone. */
export function RewindTimeline({ sessionId }: { sessionId: string }) {
  const t = useT();
  const qc = useQueryClient();
  const [picked, setPicked] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const points = useQuery({
    queryKey: ["code-undo-points", sessionId],
    queryFn: () => listCodeUndoPoints(sessionId),
    staleTime: 0,
  });
  const turns = points.data?.turns ?? [];
  const point = turns.find((item) => item.turn_id === picked) ?? null;

  function refresh() {
    void points.refetch();
    void qc.invalidateQueries({ queryKey: ["fs-tree"] });
  }

  async function redo() {
    const result = await redoCodeUndo(sessionId);
    setNotice(result.ok ? t("code.undo.redone", { n: result.restored }) : t("code.undo.unavailable"));
    refresh();
  }

  return (
    <section className="border-b border-hairline px-3 py-2" aria-label={t("code.undo.timeline")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{t("code.undo.timeline")}</h3>
        {notice ? (
          <p role="status" data-testid="undo-notice" className="text-xs text-warn-foreground">
            {notice}
          </p>
        ) : null}
        <Button
          size="sm"
          variant="ghost"
          disabled={!points.data?.can_redo || points.isFetching}
          data-testid="undo-redo"
          onClick={() => void redo()}
        >
          <Undo2 className="h-3.5 w-3.5" /> {t("code.undo.redo")}
        </Button>
      </div>
      {points.isLoading ? <p className="mt-2 text-xs text-muted-foreground">{t("common.loading")}</p> : null}
      {points.isError ? (
        <p role="alert" className="mt-2 text-xs text-warn-foreground">
          {t("code.undo.loadFailed")}
        </p>
      ) : null}
      <ol className="mt-2 flex flex-wrap gap-2">
        {turns.map((item, index) => (
          <li key={item.turn_id}>
            <Button
              size="sm"
              variant="ghost"
              disabled={item.available === false}
              title={item.available === false ? (item.reason ?? t("code.undo.unavailable")) : t("code.undo.rewind")}
              aria-label={`${t("code.undo.rewind")}: ${t("code.undo.turnLabel", { n: index + 1 })}`}
              data-testid={`undo-point-${item.turn_id}`}
              onClick={() => {
                setPicked(item.turn_id);
                setNotice("");
              }}
            >
              <History className="h-3.5 w-3.5" /> {t("code.undo.turnLabel", { n: index + 1 })} ·{" "}
              {new Date(item.created_at * 1000).toLocaleTimeString()} ·{" "}
              {t("code.undo.fileCount", { n: item.files.length })}
            </Button>
          </li>
        ))}
      </ol>
      {points.data?.cutoff ? <p className="mt-1 text-xs text-muted-foreground">{t("code.undo.trimmed")}</p> : null}
      {points.isSuccess && turns.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">{t("code.undo.empty")}</p>
      ) : null}
      {point ? (
        <RewindDialog
          sessionId={sessionId}
          point={point}
          onClose={() => setPicked(null)}
          onDone={(message) => {
            setNotice(message);
            setPicked(null);
            refresh();
          }}
        />
      ) : null}
    </section>
  );
}
