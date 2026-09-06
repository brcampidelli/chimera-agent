import { useState } from "react";
import { ShieldQuestion } from "lucide-react";
import { answerApproval } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/**
 * The question a turn is parked on, with the two answers.
 *
 * Before this the same situation reached the screen as `"{n} write(s) refused"` after the turn had
 * already finished — a count, no reason, nothing to do about it. The tool call is now waiting on a
 * worker thread for the answer, so this card is not a notice: it is the other half of a pause. The
 * reason is the taint ledger's own sentence, shown verbatim; silence refuses after `wait_seconds`,
 * and the card says so, because a control that quietly expires is a control that lied about being
 * one.
 *
 * `ok: false` on answering is a stale click — the question timed out or was answered from the CLI —
 * and the card simply goes away; there is nothing else a late press could mean.
 */
/** What the card needs of a question. The stream frame carries `wait_seconds`; a question listed
 *  from `GET /api/approvals` after a reload does not know how long its turn will wait, and the card
 *  says nothing rather than a number it does not have. */
export interface ApprovalQuestionLike {
  id: string;
  action: string;
  reason: string;
  wait_seconds?: number;
}

export function ApprovalCard({
  question,
  onAnswered,
}: {
  question: ApprovalQuestionLike;
  onAnswered: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const answer = async (approved: boolean) => {
    setBusy(true);
    try {
      await answerApproval(question.id, approved);
    } finally {
      setBusy(false);
      onAnswered();
    }
  };
  return (
    <div
      role="group"
      aria-label={t("code.approval.title")}
      className="mb-2 rounded-xl border border-accent/40 bg-accent/5 px-3 py-2 text-sm"
    >
      <div className="flex items-center gap-2 font-medium">
        <ShieldQuestion className="h-4 w-4 text-accent-foreground" aria-hidden="true" />
        {t("code.approval.title")}
      </div>
      <p className="mt-1 text-muted-foreground">{question.reason}</p>
      {question.action ? (
        <p className="mt-1 font-mono text-xs text-muted-foreground">{question.action}</p>
      ) : null}
      {question.wait_seconds !== undefined ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {t("code.approval.expires", { s: Math.round(question.wait_seconds) })}
        </p>
      ) : null}
      <div className="mt-2 flex gap-2">
        <Button size="sm" disabled={busy} onClick={() => void answer(true)}>
          {t("code.approval.approve")}
        </Button>
        <Button size="sm" variant="outline" disabled={busy} onClick={() => void answer(false)}>
          {t("code.approval.refuse")}
        </Button>
      </div>
    </div>
  );
}
