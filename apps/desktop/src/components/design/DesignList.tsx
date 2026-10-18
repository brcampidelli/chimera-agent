import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, Trash2 } from "lucide-react";

import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { focusRing } from "@/components/ui/focus";
import { Spinner } from "@/components/ui/panel";
import { ApiError, createDesign, deleteDesign, listDesigns, type DesignSummary } from "@/lib/api";
import { useNum, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export const DESIGNS_QUERY = ["designs"] as const;

/** A title for a design made from a brief: its first line, short. The server caps it too. */
export function titleFromBrief(brief: string | undefined): string {
  const line = (brief ?? "").trim().split("\n")[0] ?? "";
  return line.length > 60 ? `${line.slice(0, 59)}…` : line;
}

/**
 * The person's designs, in the viewer when no design is open: where the palette's "Open design"
 * lands and where a conversation's "Open in Chimera Design" lands before the agent has made one.
 * Every state is said: loading, failed (with retry), not allowed, and empty with the one action
 * that fixes it.
 */
export function DesignList({
  brief,
  onOpen,
}: {
  /** A brief the conversation carried here; "New design" starts from it. */
  brief?: string;
  onOpen: (id: string, brief?: string) => void;
}) {
  const t = useT();
  const num = useNum();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: DESIGNS_QUERY, queryFn: listDesigns });
  const [doomed, setDoomed] = useState<DesignSummary | null>(null);
  const create = useMutation({
    mutationFn: () => createDesign(titleFromBrief(brief)),
    onSuccess: (doc) => {
      void qc.invalidateQueries({ queryKey: DESIGNS_QUERY });
      onOpen(doc.id, brief);
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteDesign(id),
    onSuccess: () => {
      setDoomed(null);
      void qc.invalidateQueries({ queryKey: DESIGNS_QUERY });
    },
  });
  const denied = list.error instanceof ApiError && (list.error.status === 401 || list.error.status === 403);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="min-w-0 flex-1 text-sm font-semibold text-foreground">{t("design.list.title")}</h2>
        <Button type="button" size="sm" onClick={() => create.mutate()} disabled={create.isPending}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          {t(brief ? "design.list.newFromBrief" : "design.list.new")}
        </Button>
      </div>
      {brief ? (
        <blockquote className="rounded-card border border-hairline bg-surface-2 px-3 py-2 text-sm text-foreground">
          <span className="block text-xs text-muted-foreground">{t("design.list.brief")}</span>
          <span className="line-clamp-3 break-words">{brief}</span>
        </blockquote>
      ) : null}
      {create.isError ? <p className="text-sm text-bad-foreground">{t("design.list.createError")}</p> : null}
      {list.isLoading ? (
        <Spinner />
      ) : denied ? (
        <p className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
          <Lock className="h-4 w-4" aria-hidden="true" />
          {t("design.list.denied")}
        </p>
      ) : list.isError ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : (list.data ?? []).length === 0 ? (
        <p className="py-6 text-sm text-muted-foreground">{t("design.list.empty")}</p>
      ) : (
        <ul className="flex flex-col divide-y divide-hairline" aria-label={t("design.list.title")}>
          {(list.data ?? []).map((d) => (
            <li key={d.id} className="flex items-center gap-2 py-1.5">
              <button
                type="button"
                onClick={() => onOpen(d.id)}
                className={cn("min-w-0 flex-1 rounded-lg px-2 py-1 text-left transition-colors duration-1 ease-out hover:bg-surface-hover", focusRing)}
              >
                <span className="block truncate text-sm text-foreground">{d.title}</span>
                <span className="block text-xs text-muted-foreground">
                  {t("design.list.meta", { n: num(d.revision), date: d.updated_at.slice(0, 10) })}
                </span>
              </button>
              <button
                type="button"
                aria-label={t("design.list.delete", { title: d.title })}
                onClick={() => setDoomed(d)}
                className={cn("inline-flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground transition-colors duration-1 ease-out hover:bg-surface-hover", focusRing)}
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <Dialog
        open={doomed !== null}
        onOpenChange={(open) => !open && setDoomed(null)}
        title={t("design.list.deleteTitle")}
        description={t("design.list.deleteBody", { title: doomed?.title ?? "" })}
        footer={
          <>
            <Button type="button" variant="ghost" onClick={() => setDoomed(null)}>
              {t("common.cancel")}
            </Button>
            <Button type="button" disabled={remove.isPending} onClick={() => doomed && remove.mutate(doomed.id)}>
              {t("design.list.deleteConfirm")}
            </Button>
          </>
        }
      >
        {remove.isError ? <p className="text-sm text-bad-foreground">{t("design.list.deleteError")}</p> : null}
      </Dialog>
    </div>
  );
}
