import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Square } from "lucide-react";

import { listJobs, stopJob } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { BackgroundJob } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The agent's background shell jobs: commands it started with `run_shell(background=true)` and did
 * not wait for — a benchmark stage, a long build. They outlive the turn that started them and the
 * turn's Stop does not reach them, so the one place a person can see and stop them has to be a list
 * that does not depend on any conversation being open. This is that list.
 *
 * Renders nothing while there are no jobs: an empty section beside every conversation would be a
 * box that is almost always empty. Polled, like the machine panel beside it, and for the same reason
 * — a second event stream for a side panel is a second thing that can fail mid-turn.
 */
/** `bare` drops the panel's own frame and title, for a dock that draws both (dynamic screen, phase 4). */
export function JobsPanel({ bare = false }: { bare?: boolean } = {}) {
  const t = useT();
  const queryClient = useQueryClient();
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    refetchInterval: 5000,
    staleTime: 0,
  });
  const stop = useMutation({
    mutationFn: (jobId: string) => stopJob(jobId),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  const list = jobs.data?.jobs ?? [];
  if (list.length === 0) return null;

  return (
    <div className={bare ? undefined : "border-t border-hairline px-4 py-3"}>
      {bare ? null : (
        <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t("jobs.title")}
        </div>
      )}
      <ul className="space-y-2">
        {list.map((job) => (
          <JobRow
            key={job.id}
            job={job}
            stopping={stop.isPending && stop.variables === job.id}
            onStop={() => stop.mutate(job.id)}
          />
        ))}
      </ul>
    </div>
  );
}

const STATE_TONE: Record<string, string> = {
  running: "text-accent-ink",
  finished: "text-ok-foreground",
  cancelled: "text-muted-foreground",
  timed_out: "text-bad-foreground",
  lost: "text-bad-foreground",
};

function JobRow({
  job,
  stopping,
  onStop,
}: {
  job: BackgroundJob;
  stopping: boolean;
  onStop: () => void;
}) {
  const t = useT();
  const state = job.state;
  const started = new Date(job.started_at * 1000).toLocaleTimeString();
  const failed = state === "finished" && job.exit_code != null && job.exit_code !== 0;
  return (
    <li className="text-sm" data-testid={`job-${job.id}`}>
      <div className="flex items-center gap-2">
        <span
          className={cn("text-xs font-medium", failed ? "text-bad-foreground" : STATE_TONE[state] ?? "")}
        >
          {t(`jobs.state.${state}`)}
          {job.exit_code != null ? ` · ${t("jobs.exit", { code: job.exit_code })}` : ""}
        </span>
        <span className="text-xs text-muted-foreground">{t("jobs.started", { time: started })}</span>
        {state === "running" ? (
          <button
            type="button"
            onClick={onStop}
            disabled={stopping}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-surface-2 hover:text-foreground disabled:opacity-50"
            aria-label={t("jobs.stopLabel", { id: job.id })}
          >
            <Square className="h-3 w-3" />
            {t("jobs.stop")}
          </button>
        ) : null}
      </div>
      <div className="truncate font-mono text-xs" title={job.command}>
        {job.command}
      </div>
      {job.tail ? (
        <details className="mt-0.5">
          <summary className="cursor-pointer text-xs text-muted-foreground">
            {t("jobs.output")}
          </summary>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md bg-surface-2 p-1.5 font-mono text-xs">
            {job.tail}
          </pre>
        </details>
      ) : null}
    </li>
  );
}
