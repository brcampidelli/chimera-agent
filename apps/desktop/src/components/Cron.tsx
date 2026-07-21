import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Plus, Trash2 } from "lucide-react";
import { createCron, deleteCron, disableCron, enableCron, getCron } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

const PRESETS: { key: string; cron: string }[] = [
  { key: "cron.preset.morning", cron: "0 7 * * *" },
  { key: "cron.preset.hourly", cron: "0 * * * *" },
  { key: "cron.preset.weekdays", cron: "0 9 * * 1-5" },
];

function AddSchedule() {
  const t = useT();
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [schedule, setSchedule] = useState("0 7 * * *");
  const [action, setAction] = useState("");
  const create = useMutation({
    mutationFn: createCron,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cron"] });
      setName("");
      setAction("");
    },
  });
  const canSubmit = name.trim() && schedule.trim() && action.trim() && !create.isPending;

  return (
    <Panel title={t("cron.add.title")}>
      <div className="space-y-2 px-4 py-3">
        <input
          className="field w-full px-3 py-2 text-sm"
          placeholder={t("cron.add.name")}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <textarea
          className="field w-full px-3 py-2 text-sm"
          rows={2}
          placeholder={t("cron.add.action")}
          value={action}
          onChange={(e) => setAction(e.target.value)}
        />
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span>{t("cron.add.presets")}</span>
          {PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => setSchedule(p.cron)}
              className={`rounded-chip border border-white/10 px-2 py-0.5 transition hover:brightness-110 ${
                schedule === p.cron ? "bg-accent-grad text-white" : "bg-muted"
              }`}
            >
              {t(p.key)}
            </button>
          ))}
        </div>
        <input
          className="field w-full px-3 py-2 font-mono text-xs"
          placeholder={t("cron.add.when")}
          value={schedule}
          onChange={(e) => setSchedule(e.target.value)}
        />
        <div className="flex items-center justify-between gap-3 pt-0.5">
          <span className="text-xs text-muted-foreground">{t("cron.add.hint")}</span>
          <Button
            onClick={() => canSubmit && create.mutate({ name, schedule, action })}
            disabled={!canSubmit}
          >
            <Plus className="h-3.5 w-3.5" /> {t("cron.add.submit")}
          </Button>
        </div>
        {create.isError && <div className="text-xs text-bad">{t("cron.add.error")}</div>}
      </div>
    </Panel>
  );
}

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
      <AddSchedule />
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
