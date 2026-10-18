import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import type { DesignSession } from "@/components/design/useDesignSession";
import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/panel";
import { ApiError, exportDesign, listDesignVersions, type DesignVersion } from "@/lib/api";
import { useNum, useT } from "@/lib/i18n";

/**
 * The formats the export route writes. Labels are written out per format so the i18n reachability
 * check sees every key. PDF and PNG need Chromium and PPTX needs python-pptx on the machine; when one
 * is missing the route refuses with what to install, and the dialog shows that sentence.
 */
export const EXPORT_FORMATS = [
  { id: "html", label: "design.export.format.html" },
  { id: "pdf", label: "design.export.format.pdf" },
  { id: "png", label: "design.export.format.png" },
  { id: "pptx", label: "design.export.format.pptx" },
] as const;

/** Save a blob as a file. Same approach as the memory export: an anchor with `download`. */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Exporting writes the design to a file that leaves the app, so it is asked first: which format, and
 * a yes. Only that yes sends `confirm: true`.
 */
export function ExportDialog({
  open,
  onOpenChange,
  designId,
  session,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  designId: string;
  session: DesignSession;
}) {
  const t = useT();
  const id = useId();
  const [format, setFormat] = useState<string>(EXPORT_FORMATS[0].id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await session.flush();
      const file = await exportDesign(designId, format);
      saveBlob(file.blob, file.filename);
      onOpenChange(false);
    } catch (e) {
      // A missing renderer answers 503 with a sentence naming what to install: show it as written.
      setError(e instanceof ApiError && e.status === 503 && e.message ? e.message : t("design.export.error"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("design.export.title")}
      description={t("design.export.body")}
      footer={
        <>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button type="button" disabled={busy} onClick={() => void run()}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            {t("design.export.confirm")}
          </Button>
        </>
      }
    >
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {t("design.export.format")}
      </label>
      <select id={id} value={format} onChange={(e) => setFormat(e.target.value)} className="field mt-1 h-8 px-2 text-sm">
        {EXPORT_FORMATS.map((f) => (
          <option key={f.id} value={f.id}>
            {t(f.label)}
          </option>
        ))}
      </select>
      {error ? <p role="alert" className="mt-2 text-sm text-bad-foreground">{error}</p> : null}
    </Dialog>
  );
}

/**
 * The saved revisions, newest first, each restorable. Restoring replaces what is on the canvas (the
 * current content stays in the history, and the restore itself is one step to undo), so it asks
 * first, naming the revision.
 */
export function VersionsDialog({
  open,
  onOpenChange,
  designId,
  session,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  designId: string;
  session: DesignSession;
}) {
  const t = useT();
  const num = useNum();
  const versions = useQuery({
    queryKey: ["design-versions", designId, session.savedRevision],
    queryFn: () => listDesignVersions(designId),
    enabled: open,
  });
  const [chosen, setChosen] = useState<DesignVersion | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function restore(): Promise<void> {
    if (!chosen) return;
    setBusy(true);
    setFailed(false);
    const ok = await session.restore(chosen.revision);
    setBusy(false);
    if (!ok) return setFailed(true);
    setChosen(null);
    onOpenChange(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setChosen(null);
        onOpenChange(next);
      }}
      title={chosen ? t("design.versions.confirmTitle", { n: num(chosen.revision) }) : t("design.versions.title")}
      description={chosen ? t("design.versions.confirmBody") : undefined}
      footer={
        chosen ? (
          <>
            <Button type="button" variant="ghost" onClick={() => setChosen(null)}>
              {t("common.cancel")}
            </Button>
            <Button type="button" disabled={busy} onClick={() => void restore()}>
              {t("design.versions.confirm")}
            </Button>
          </>
        ) : undefined
      }
    >
      {failed ? <p role="alert" className="text-sm text-bad-foreground">{t("design.versions.error")}</p> : null}
      {chosen ? null : versions.isLoading ? (
        <Spinner />
      ) : versions.isError ? (
        <ErrorState error={versions.error} onRetry={() => void versions.refetch()} />
      ) : (
        <ul className="flex max-h-80 flex-col divide-y divide-hairline overflow-y-auto">
          {(versions.data ?? []).map((v) => (
            <li key={v.revision} className="flex items-center gap-2 py-1.5">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-foreground">{t("design.versions.row", { n: num(v.revision), title: v.title })}</span>
                <span className="block text-xs text-muted-foreground">{v.updated_at.slice(0, 16).replace("T", " ")}</span>
              </span>
              {v.revision === session.savedRevision ? (
                <span className="text-xs text-muted-foreground">{t("design.versions.current")}</span>
              ) : (
                <Button type="button" size="sm" variant="outline" onClick={() => setChosen(v)}>
                  {t("design.versions.restore")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}
