import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Plus, Trash2 } from "lucide-react";
import { createCron, deleteCron, disableCron, enableCron, getCron } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/lib/i18n";
import { readWorkspace } from "@/lib/workspace";

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
              className={`rounded-chip border border-border px-2 py-0.5 transition hover:brightness-110 ${
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
            onClick={() =>
              // The folder the job will work in, fixed at the moment it is written rather than
              // read when it fires: a schedule runs for months, and "whichever project was open
              // at 7am" is not a root anybody chose.
              canSubmit && create.mutate({ name, schedule, action, workspace: readWorkspace() || null })
            }
            disabled={!canSubmit}
          >
            <Plus className="h-3.5 w-3.5" /> {t("cron.add.submit")}
          </Button>
        </div>
        {create.isError && <div className="text-xs text-bad-foreground">{t("cron.add.error")}</div>}
      </div>
    </Panel>
  );
}

export function Cron({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const qc = useQueryClient();
  const jobs = useQuery({ queryKey: ["cron"], queryFn: getCron });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["cron"] });
  const enable = useMutation({ mutationFn: enableCron, onSuccess: invalidate });
  const disable = useMutation({ mutationFn: disableCron, onSuccess: invalidate });
  const remove = useMutation({ mutationFn: deleteCron, onSuccess: invalidate });

  return (
    <Screen title={t("cron.title")} icon={<Clock className="h-5 w-5" />} embedded={embedded}>
      <AddSchedule />
      <Panel title={t("cron.jobs")}>
        {jobs.isError ? (
          <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} />
        ) : jobs.isLoading ? (
          <Spinner />
        ) : !jobs.data || jobs.data.length === 0 ? (
          <EmptyState text={t("cron.empty")} />
        ) : (
          jobs.data.map((j) => (
            <div key={j.id} className="group flex items-center gap-3 px-4 py-3">
              <Switch
                checked={j.enabled}
                onChange={() => (j.enabled ? disable : enable).mutate(j.id)}
                label={j.enabled ? t("cron.disable") : t("cron.enable")}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{j.name}</span>
                  <Badge tone="muted">{j.trigger}</Badge>
                  {j.created_by === "agent" && <Badge tone="accent">{t("cron.agent")}</Badge>}
                  {/* A job that has been failing every hour for a week used to render identically
                      to one that works. The API has carried `consecutive_failures` and `last_error`
                      the whole time; this screen showed neither, so the only way to find out was to
                      read the scheduler log — which is exactly the thing a schedule screen exists to
                      save you from. */}
                  {j.consecutive_failures > 0 && (
                    <Badge tone="bad">{t("cron.failing", { n: j.consecutive_failures })}</Badge>
                  )}
                </div>
                <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                  {j.schedule} → {j.action}
                </div>
                {/* Which folder it works in. Only when there is one: a job with no workspace runs
                    at whatever root the app was started with, and printing a guess for that is
                    worse than the blank — it is the difference this row exists to make visible. */}
                {j.workspace && (
                  <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground" title={j.workspace}>
                    {j.workspace}
                  </div>
                )}
                {/* Inline, not a tooltip. WHY it failed is the whole reason to look at this row, and
                    a tooltip is found by accident. Truncated to one line: the full text is in the
                    title, and a stack trace must not push every other job off the screen. */}
                {j.consecutive_failures > 0 && j.last_error && (
                  <div className="mt-0.5 truncate font-mono text-xs text-bad-foreground" title={j.last_error}>
                    {j.last_error}
                  </div>
                )}
              </div>
              <button
                className="opacity-0 transition focus:opacity-100 group-hover:opacity-100"
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
