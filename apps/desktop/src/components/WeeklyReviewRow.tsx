import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Switch } from "@/components/ui/switch";
import { getWeeklyReview, putWeeklyReview } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { WeeklyReview } from "@/lib/types";

/**
 * The weekly review's switch, in the Automation card (`chimera/scheduler/weekly_review.py`).
 *
 * It shipped as `chimera report weekly` (which proposes the job, disabled) and `chimera cron enable`
 * (which switches it on) — two terminal commands for a person who may never open one. On proposes
 * the job if it is not there and enables it; off pauses it and never creates one. Once it exists it
 * is an ordinary job on the Automation screen too, with the same switch.
 *
 * Where it posts stays the CLI's: the destination is a webhook URL, which is a credential, and the
 * row shows its host only. With none, the row says the result stays in Automation's results — the
 * honest answer to "where will I read it", rather than a switch that seems to send it somewhere.
 */
export function WeeklyReviewRow() {
  const t = useT();
  const qc = useQueryClient();
  const review = useQuery({ queryKey: ["weekly-review"], queryFn: () => getWeeklyReview(), retry: false });
  const mutation = useMutation({
    mutationFn: (enabled: boolean) => putWeeklyReview(enabled),
    onSuccess: (next: WeeklyReview) => {
      qc.setQueryData(["weekly-review"], next);
      // The job list on the Automation screen gains (or changes) a row.
      qc.invalidateQueries({ queryKey: ["cron"] });
    },
  });
  const r = review.data;
  // A server older than the route answers 404: no row rather than a switch that cannot save.
  if (!r) return null;
  const label = t("settings.row.weeklyReview");
  const where = r.proposed
    ? r.posts_to
      ? t("settings.weeklyReview.postsTo", { host: r.posts_to })
      : t("settings.weeklyReview.nowhere")
    : "";

  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{t("settings.hint.weeklyReview")}</div>
        {where ? <div className="text-xs text-muted-foreground">{where}</div> : null}
        {mutation.isError && (
          <div role="alert" className="text-xs text-bad-foreground">
            {mutation.error instanceof Error ? mutation.error.message : String(mutation.error)}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Switch
          checked={r.proposed && (r.enabled ?? false)}
          disabled={mutation.isPending}
          label={label}
          onChange={(next) => mutation.mutate(next)}
        />
      </div>
    </div>
  );
}
