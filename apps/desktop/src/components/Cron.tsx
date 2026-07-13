import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Trash2 } from "lucide-react";
import { deleteCron, disableCron, enableCron, getCron } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";

function Toggle({ on, onChange }: { on: boolean; onChange: () => void }) {
  const t = useT();
  return (
    <button
      onClick={onChange}
      className={`relative h-5 w-9 rounded-chip transition-all ${
        on ? "bg-accent-grad shadow-[0_0_12px_-2px_hsl(var(--accent)/0.75)]" : "bg-muted shadow-inset"
      }`}
      role="switch"
      aria-checked={on}
      title={on ? t("cron.disable") : t("cron.enable")}
    >
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-all ${on ? "left-4" : "left-0.5"}`} />
    </button>
  );
}

export function Cron() {
  const t = useT();
  const qc = useQueryClient();
  const jobs = useQuery({ queryKey: ["cron"], queryFn: getCron });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["cron"] });
  const enable = useMutation({ mutationFn: enableCron, onSuccess: invalidate });
  const disable = useMutation({ mutationFn: disableCron, onSuccess: invalidate });
  const remove = useMutation({ mutationFn: deleteCron, onSuccess: invalidate });

  return (
    <Screen title={t("cron.title")} icon={<Clock className="h-5 w-5" />}>
      <Panel title={t("cron.jobs")}>
        {jobs.isLoading ? (
          <Spinner />
        ) : !jobs.data || jobs.data.length === 0 ? (
          <EmptyState text={t("cron.empty")} />
        ) : (
          jobs.data.map((j) => (
            <div key={j.id} className="group flex items-center gap-3 px-4 py-3">
              <Toggle on={j.enabled} onChange={() => (j.enabled ? disable : enable).mutate(j.id)} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{j.name}</span>
                  <Badge tone="muted">{j.trigger}</Badge>
                  {j.created_by === "agent" && <Badge tone="accent">{t("cron.agent")}</Badge>}
                </div>
                <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                  {j.schedule} → {j.action}
                </div>
              </div>
              <button
                className="opacity-0 transition group-hover:opacity-100"
                title={t("common.delete")}
                onClick={() => remove.mutate(j.id)}
              >
                <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-bad" />
              </button>
            </div>
          ))
        )}
      </Panel>
    </Screen>
  );
}
