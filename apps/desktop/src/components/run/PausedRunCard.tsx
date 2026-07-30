import { useState } from "react";
import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { respondRun, type HitlAction, type PausedRun } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const fieldCls = "field w-full px-3 text-sm";

/**
 * A run waiting on a human.
 *
 * The four actions are the core's HITL envelope, which has existed since M13 and until now was
 * reachable only from the CLI — the app could arm a pause it had no way to answer.
 *
 * None of them concludes the run by itself. Recording the verdict only writes it to the
 * checkpoint; the run finalizes when it is resumed with the same thread. That is why every button
 * here hands the decision back to the caller to resume, rather than pretending it finished.
 */
export function PausedRunCard({
  run,
  onResolved,
}: {
  run: PausedRun;
  /** Called once the verdict is recorded. `retries` is true only for "respond", which spends
   *  another attempt — the caller resumes the thread either way. */
  onResolved: (threadId: string, action: HitlAction, retries: boolean) => void;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [answer, setAnswer] = useState(run.answer);
  const [feedback, setFeedback] = useState("");
  const [sending, setSending] = useState(false);

  async function decide(action: HitlAction) {
    if (sending) return;
    setSending(true);
    try {
      const out = await respondRun(run.thread_id, action, {
        answer: action === "edit" ? answer : undefined,
        feedback: action === "respond" ? feedback : undefined,
      });
      if (out.ok) onResolved(run.thread_id, action, out.retries);
    } finally {
      setSending(false);
    }
  }

  return (
    <div
      // An open question, not an alert: nothing is broken and nothing is lost. `role="region"` with
      // a name, so a screen reader can find it again after the run's own log has moved on.
      role="region"
      aria-label={t("runs.pausedTitle")}
      className="space-y-3 rounded-chip border border-warn/40 bg-surface-2 p-3"
    >
      <div className="flex items-center gap-2 text-sm font-medium">
        <AlertTriangle className="h-4 w-4 text-warn" aria-hidden />
        {t("runs.pausedTitle")}
      </div>
      <p className="text-xs text-muted-foreground">{t("runs.pausedNote")}</p>

      <div className="space-y-1">
        <div className="text-xs text-muted-foreground">{t("runs.pausedAnswer")}</div>
        {editing ? (
          <textarea
            className={cn(fieldCls, "min-h-[96px] resize-y py-2 font-mono text-xs")}
            aria-label={t("runs.pausedAnswer")}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
          />
        ) : (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-chip bg-card/60 p-2 font-mono text-xs">
            {run.answer}
          </pre>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {editing ? (
          <Button size="sm" disabled={sending} onClick={() => void decide("edit")}>
            {t("runs.accept")}
          </Button>
        ) : (
          <>
            <Button size="sm" disabled={sending} onClick={() => void decide("accept")}>
              {t("runs.accept")}
            </Button>
            <Button size="sm" variant="ghost" disabled={sending} onClick={() => setEditing(true)}>
              {t("runs.editAnswer")}
            </Button>
          </>
        )}
        <Button size="sm" variant="ghost" disabled={sending} onClick={() => void decide("ignore")}>
          {t("runs.ignore")}
        </Button>
      </div>

      <div className="space-y-1.5 border-t border-hairline pt-2.5">
        <input
          className={cn(fieldCls, "h-9 text-xs")}
          placeholder={t("runs.feedbackPlaceholder")}
          aria-label={t("runs.feedbackPlaceholder")}
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
        />
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={sending || !feedback.trim()}
            onClick={() => void decide("respond")}
          >
            {t("runs.respondAgain")}
          </Button>
          {/* Said before the click, not after: this is the only action that spends a model call. */}
          <span className="text-xs text-muted-foreground">{t("runs.retryCost")}</span>
        </div>
      </div>
    </div>
  );
}
