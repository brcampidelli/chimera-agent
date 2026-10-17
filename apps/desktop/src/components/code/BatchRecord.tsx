import type { ComponentProps } from "react";
import { AlertTriangle, Boxes } from "lucide-react";

import { SafeMarkdown } from "@/components/markdown/SafeMarkdown";
import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { AgentBatchNote, AgentsBatch } from "@/lib/types";

type Tone = NonNullable<ComponentProps<typeof Badge>["tone"]>;

/** How each way a task's loop can end reads (`AutonomousResult.ending`). `no_op` is a task that
 *  answered without editing: done, which is what an analysis task is when it works. */
const ENDING_TONE: Record<string, Tone> = {
  success: "ok",
  no_op: "ok",
  exhausted: "bad",
  denied: "bad",
  cancelled: "warn",
  spend: "warn",
  paused: "warn",
  handover: "warn",
  unknown: "muted",
};

/** The ending as a badge. Nothing for a task with no result (it has an error instead), and an ending
 *  this build does not know is shown as "unknown" rather than as a word with no translation. */
export function EndingBadge({ ending }: { ending: string }) {
  const t = useT();
  if (!ending) return null;
  const known = ending in ENDING_TONE ? ending : "unknown";
  return (
    <Badge tone={ENDING_TONE[known]} title={ending}>
      {t(`agents.ending.${known}`)}
    </Badge>
  );
}

/** Past this many characters an answer starts folded, its first lines showing what it is about. */
const FOLD_AT = 600;

/**
 * A task's own final answer, as markdown. The agents' text is drawn the way a turn's answer is (the
 * safe renderer: no raw HTML, links vetted) and without the local images a turn may show: a batch
 * task's answer is not the conversation's own. Long answers start folded.
 */
export function TaskAnswer({ answer }: { answer: string }) {
  const t = useT();
  if (!answer.trim()) return null;
  const body = (
    <div data-task-answer className="md min-w-0 text-sm leading-relaxed text-foreground/90">
      <SafeMarkdown allowLocalImages={false}>{answer}</SafeMarkdown>
    </div>
  );
  if (answer.length <= FOLD_AT) return body;
  return (
    <details className="rounded-chip bg-surface-2">
      <summary className="cursor-pointer px-2 py-1.5 text-xs text-muted-foreground hover:text-foreground">
        {t("agents.answer.more")} · {answer.slice(0, 120).replace(/\s+/g, " ").trim()}…
      </summary>
      <div className="px-2 pb-2">{body}</div>
    </details>
  );
}

/** The record a finished batch leaves in its conversation, from its terminal frame: the shape the
 *  server stores (`agent_batch_note.batch_record`), so a batch drawn live and one read back match. */
export function batchNoteOf(batch: AgentsBatch, at: string = new Date().toISOString()): AgentBatchNote {
  return {
    tasks: batch.results.map((r) => ({
      index: r.index,
      task: r.task,
      success: r.success,
      ending: r.ending,
      answer: r.answer,
      changed_paths: r.changed_paths,
      error: r.error,
    })),
    conflicts: batch.conflicts,
    merged: batch.merged,
    is_repo: batch.is_repo,
    at,
  };
}

/**
 * A finished batch in the conversation (the Conversa view), after the exchange that confirmed it:
 * per task its title, how it ended, its answer and what it changed, then the conflicts. Not a turn
 * and not drawn as one: nobody typed it, and its words are the batch's agents', said so above them.
 */
export function BatchRecord({ note }: { note: AgentBatchNote }) {
  const t = useT();
  return (
    <section
      aria-label={t("code.batch.title", { n: note.tasks.length })}
      data-batch-record
      className="space-y-2 rounded-chip border border-hairline bg-card/40 p-3"
    >
      <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
        <Boxes className="h-3.5 w-3.5 text-accent" aria-hidden />
        {t("code.batch.title", { n: note.tasks.length })}
      </div>
      <p className="text-xs text-muted-foreground">{t("code.batch.note")}</p>
      {!note.is_repo ? (
        <p className="flex items-start gap-2 text-xs text-warn-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {t("agents.notRepoBanner")}
        </p>
      ) : null}
      <ol className="space-y-2">
        {note.tasks.map((task) => (
          <li key={task.index} className="space-y-1.5 border-t border-hairline pt-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted-foreground">#{task.index + 1}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-foreground" title={task.task}>
                {task.task || t("agents.untitled")}
              </span>
              <EndingBadge ending={task.ending} />
            </div>
            {task.error ? <p className="font-mono text-xs text-bad-foreground">{task.error}</p> : null}
            <TaskAnswer answer={task.answer} />
            {task.changed_paths.length > 0 ? (
              <p className="truncate font-mono text-xs text-muted-foreground" title={task.changed_paths.join(", ")}>
                {t("agents.changedFiles")}: {task.changed_paths.join(", ")}
              </p>
            ) : null}
          </li>
        ))}
      </ol>
      {note.conflicts.length > 0 ? (
        <div className="rounded-chip border border-bad/30 bg-bad/10 px-3 py-2 text-xs">
          <div className="font-semibold text-bad-foreground">
            {t("agents.conflictsTitle")} ({note.conflicts.length})
          </div>
          <p className="mt-1 font-mono text-foreground/80">{note.conflicts.join(", ")}</p>
        </div>
      ) : null}
    </section>
  );
}
