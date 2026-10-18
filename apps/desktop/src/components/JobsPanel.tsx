import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Inbox, Square } from "lucide-react";

import { queuePollInterval, taskCounts } from "@/components/code/side/rules";
import { listCodeTurnQueue, listJobs, stopJob } from "@/lib/api";
import { useConversationSide } from "@/lib/conversation-side";
import { useT } from "@/lib/i18n";
import type { BackgroundJob, CodeTurnQueueItem } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * What runs without anyone watching it: the agent's background shell jobs (`run_shell(background=true)`)
 * and, while a conversation is open, that conversation's durable queue of turns and its works.
 *
 * The jobs outlive the turn that started them and the turn's Stop does not reach them, so the one place
 * a person can see and stop them has to be a list that does not depend on any conversation being open.
 * This is that list. Until 0.69 the Code screen also had a "Conversation tasks" card that read the SAME
 * jobs (same query key, same 5 s) beside this one, so the right panel showed the jobs twice, under two
 * names, one of them without a Stop button. The two were merged here; the queue and the works the
 * other card added come along when there is a conversation to read them from.
 *
 * Renders nothing while there is nothing running or kept: an empty section beside every conversation
 * would be a box that is almost always empty. `alone` is the panel drawn where vanishing is not an
 * answer (maximised over the main area, or in a window of its own): there it says so in words.
 * Polled, like the machine panel beside it, and for the same reason — a second event stream for a side
 * panel is a second thing that can fail mid-turn.
 */
export interface BackgroundWork {
  jobs: BackgroundJob[];
  queue: CodeTurnQueueItem[];
  works: number;
}

/**
 * The reads the panel draws from, and the dock's reason to leave it out (`useSilentPanels`): one hook,
 * so the two can never disagree about whether there is something to show. `enabled: false` asks
 * nothing, for a panel the person closed.
 */
export function useBackgroundWork(enabled = true): BackgroundWork {
  const side = useConversationSide();
  const sessionId = side?.sessionId ?? null;
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    enabled,
    refetchInterval: 5000,
    staleTime: 0,
  });
  const queue = useQuery({
    queryKey: ["code-turn-queue", sessionId],
    queryFn: () => listCodeTurnQueue(sessionId ?? ""),
    enabled: enabled && sessionId !== null,
    refetchInterval: (query) => queuePollInterval(query.state.data?.items, document.hidden),
    refetchIntervalInBackground: false,
    retry: false,
  });
  return {
    jobs: jobs.data?.jobs ?? [],
    // A conversation's queue is drawn only while that conversation is the one on screen.
    queue: sessionId === null ? [] : queue.data?.items ?? [],
    works: side?.worksActive ?? 0,
  };
}

/** Nothing to show: no job, kept or running, no queued turn waiting or being worked, no work running. */
export function isBackgroundEmpty(work: BackgroundWork): boolean {
  const counts = taskCounts(work.jobs, work.queue, work.works, null);
  return work.jobs.length === 0 && counts.queueRunning + counts.queueWaiting === 0 && counts.works === 0;
}

/** `bare` drops the panel's own frame and title, for a dock that draws both (dynamic screen, phase 4). */
export function JobsPanel({ bare = false, alone = false }: { bare?: boolean; alone?: boolean } = {}) {
  const t = useT();
  const queryClient = useQueryClient();
  const work = useBackgroundWork();
  const stop = useMutation({
    mutationFn: (jobId: string) => stopJob(jobId),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });

  if (isBackgroundEmpty(work)) {
    return alone ? <p className="text-sm text-muted-foreground">{t("side.tasks.none")}</p> : null;
  }
  const counts = taskCounts(work.jobs, work.queue, work.works, null);

  return (
    <div className={bare ? "space-y-2" : "space-y-2 border-t border-hairline px-4 py-3"}>
      {bare ? null : (
        <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{t("jobs.title")}</div>
      )}
      {/* The count says what runs in all three sources, so it is drawn only when more than jobs are
          counted: above a lone job's own row it would repeat that row. */}
      {counts.queueRunning + counts.queueWaiting + counts.works > 0 ? (
        <p className="flex items-center gap-2 text-sm" data-testid="jobs-summary">
          {/* A still dot, not a spinner: this panel sits beside the conversation for the whole run, and a
              thing turning in the corner of the eye for an hour is the motion the design rules forbid. */}
          {counts.running > 0 ? <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-accent" /> : null}
          {counts.queueWaiting > 0
            ? t("side.tasks.runningWaiting", { n: counts.running, waiting: counts.queueWaiting })
            : t("side.tasks.running", { n: counts.running })}
        </p>
      ) : null}
      {work.jobs.length > 0 ? (
        <ul className="space-y-2">
          {work.jobs.map((job) => (
            <JobRow
              key={job.id}
              job={job}
              stopping={stop.isPending && stop.variables === job.id}
              onStop={() => stop.mutate(job.id)}
            />
          ))}
        </ul>
      ) : null}
      {counts.queueRunning + counts.queueWaiting > 0 ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Inbox aria-hidden className="h-3.5 w-3.5 shrink-0" />
          {t("side.tasks.queue", { running: counts.queueRunning, waiting: counts.queueWaiting })}
        </p>
      ) : null}
      {counts.works > 0 ? <p className="text-xs text-muted-foreground">{t("side.tasks.works", { n: counts.works })}</p> : null}
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
