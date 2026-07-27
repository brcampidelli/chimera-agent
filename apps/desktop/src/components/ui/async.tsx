import { RefreshCw } from "lucide-react";
import { useT } from "@/lib/i18n";

/** Terminal state for a failed query. Without it, screens gate their spinner on `isLoading || !data`
 *  — but on error React Query sets `isLoading` false while `data` stays undefined, so the spinner
 *  would stay up forever with no feedback. This shows a message + a manual retry instead. It's sized
 *  to drop straight into a `<Panel>` (mirrors the `<Spinner>` footprint). */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const t = useT();
  const detail = error instanceof Error ? error.message : "";
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 py-14 text-sm text-muted-foreground">
      <span>{t("common.loadError")}</span>
      {detail && <span className="max-w-md truncate text-xs opacity-60">{detail}</span>}
      <button
        type="button"
        onClick={onRetry}
        className="flex items-center gap-1.5 rounded-chip bg-surface-2 px-3 py-1.5 text-xs font-medium ring-1 ring-border transition hover:bg-surface-hover"
      >
        <RefreshCw className="h-3.5 w-3.5" />
        {t("common.retry")}
      </button>
    </div>
  );
}
