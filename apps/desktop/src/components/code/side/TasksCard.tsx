import { useQuery } from "@tanstack/react-query";
import { Inbox, TerminalSquare } from "lucide-react";

import { queuePollInterval, taskCounts } from "@/components/code/side/rules";
import { listCodeTurnQueue, listJobs } from "@/lib/api";
import { useConversationSide } from "@/lib/conversation-side";
import { useT } from "@/lib/i18n";

/**
 * What is running for this conversation and its folder without anyone watching it (C5): the shell
 * jobs the agent started in the folder (`run_shell(background=true)`), the conversation's durable
 * queue of turns (S5) — the one being worked and the ones waiting — and its background works.
 *
 * Every source is a read that already existed. The jobs are the list the "Background jobs" panel
 * polls (same key and the same 5 s), the works are the ones the conversation already holds, and the
 * queue is read every 10 s while it has something in it and every minute otherwise. React Query
 * stops every interval while the window is hidden.
 */
export function TasksCard() {
  const t = useT();
  const side = useConversationSide();
  const workspace = side?.workspace ?? "";
  const sessionId = side?.sessionId ?? null;
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: listJobs,
    enabled: side !== null,
    refetchInterval: 5000,
    staleTime: 0,
  });
  const queue = useQuery({
    queryKey: ["code-turn-queue", sessionId],
    queryFn: () => listCodeTurnQueue(sessionId ?? ""),
    enabled: side !== null && sessionId !== null,
    refetchInterval: (query) => queuePollInterval(query.state.data?.items, document.hidden),
    refetchIntervalInBackground: false,
    retry: false,
  });
  if (!side) return null;
  const counts = taskCounts(jobs.data?.jobs ?? [], queue.data?.items ?? [], side.worksActive, workspace);
  const nothing = counts.running === 0 && counts.queueWaiting === 0;
  return (
    <div className="space-y-1.5" data-testid="side-tasks">
      <p className="flex items-center gap-2 text-sm" data-testid="side-tasks-summary">
        {/* A still dot, not a spinner: this card sits beside the conversation for the whole run, and a
            thing turning in the corner of the eye for an hour is the motion the design rules forbid. */}
        {counts.running > 0 ? <span aria-hidden className="h-2 w-2 shrink-0 rounded-full bg-accent" /> : null}
        <span className={nothing ? "text-muted-foreground" : undefined}>
          {nothing
            ? t("side.tasks.none")
            : counts.queueWaiting > 0
              ? t("side.tasks.runningWaiting", { n: counts.running, waiting: counts.queueWaiting })
              : t("side.tasks.running", { n: counts.running })}
        </span>
      </p>
      {counts.jobs.length > 0 ? (
        <ul className="space-y-0.5">
          {counts.jobs.map((job) => (
            <li key={job.id} className="flex min-w-0 items-center gap-2 text-xs">
              <TerminalSquare aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate font-mono" title={job.command}>
                {job.command}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      {counts.queueRunning + counts.queueWaiting > 0 ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Inbox aria-hidden className="h-3.5 w-3.5 shrink-0" />
          {t("side.tasks.queue", { running: counts.queueRunning, waiting: counts.queueWaiting })}
        </p>
      ) : null}
      {counts.works > 0 ? (
        <p className="text-xs text-muted-foreground">{t("side.tasks.works", { n: counts.works })}</p>
      ) : null}
    </div>
  );
}
