import { useId, useRef, useState } from "react";

import { needsCodeKey } from "@/components/code/ApprovalCard";
import { TodoPanel } from "@/components/code/TodoPanel";
import { Button } from "@/components/ui/button";
import { useOptionalToast } from "@/components/ui/toast";
import { answerApproval } from "@/lib/api";
import { useConversationSide } from "@/lib/conversation-side";
import { useT } from "@/lib/i18n";

/**
 * The plan the conversation's most recent turn proposed, and the two answers the plan gate asks for (C5).
 *
 * The plan is read, never stored here: the stream's `plan` frame gave the steps, the approval question
 * the turn is parked on gives the answer, and the agent's todo list gives its own account of progress.
 * The planner writes no state per step, so the steps are a numbered list and nothing says a step is done.
 *
 * The answers are the card's answers: the same route (`answerApproval`) with the same question id. A
 * refusal here ends the turn exactly as the card's "Refuse" does; "Pedir ajustes" is that refusal under
 * the name the person needs, and its hint says the change goes in the next message.
 *
 * With no plan, a turn parked on an approval says so and points at the card; with neither, the dock
 * leaves the card out (`useSilentPanels`).
 */
export function PlanCard() {
  const t = useT();
  const toast = useOptionalToast();
  const side = useConversationSide();
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const titleRef = useRef<HTMLHeadingElement>(null);
  const hintId = useId();
  const plan = side?.plan ?? null;
  if (!plan) {
    return side?.approvalWaiting ? <p className="text-sm text-muted-foreground">{t("code.plan.approvalElsewhere")}</p> : null;
  }

  const statusLabel =
    plan.status === "awaiting"
      ? t("code.plan.status.awaiting")
      : plan.status === "refused"
        ? t("code.plan.status.refused")
        : plan.status === "on_card"
          ? t("code.plan.status.onCard")
          : t("code.plan.status.approved");
  const statusTone = plan.status === "awaiting" ? "text-accent-ink" : "text-muted-foreground";

  const answer = async (approved: boolean) => {
    if (!plan.awaitingId) return;
    const id = plan.awaitingId;
    setBusy(true);
    setNotice("");
    try {
      const answered = await answerApproval(id, approved);
      if (answered.ok === false) {
        // Stale: the question was answered or timed out elsewhere. The card on screen may be a different
        // question now, so nothing here is cleared; the person is told why the buttons did nothing.
        setNotice(t("code.plan.alreadyAnswered"));
        return;
      }
      if (answered.outcome === "needs_code") {
        // Asked from another process: only that process holds the code. The question stays, and the
        // sentence is the one the card gives for the same refusal.
        toast(t(needsCodeKey(answered.reason), { command: answered.detail ?? "" }), "bad");
        return;
      }
      plan.clearAwaiting(id, approved ? "approved" : "refused");
      titleRef.current?.focus();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      toast(t("code.approval.answerFailed", { detail }), "bad");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="space-y-2 text-sm" aria-label={t("code.plan.title")} data-testid="side-plan">
      <div className="flex items-baseline justify-between gap-2">
        <h3 ref={titleRef} tabIndex={-1} className="font-medium outline-none">
          {t("code.plan.title")}
        </h3>
        <p role="status" className={`text-xs ${statusTone}`}>
          {statusLabel}
        </p>
      </div>
      {notice ? (
        <p role="status" className="text-xs text-warn-foreground">
          {notice}
        </p>
      ) : null}
      <ol className="list-decimal space-y-1 pl-5">
        {plan.steps.map((step, i) => (
          <li key={i}>{step}</li>
        ))}
      </ol>
      <p className="text-xs text-muted-foreground">{t("code.plan.noStepState")}</p>
      {plan.todos.length > 0 ? <TodoPanel items={plan.todos} /> : null}
      {plan.awaitingId ? (
        <div className="space-y-1.5">
          <p id={hintId} className="text-xs text-muted-foreground">
            {t("code.plan.awaitingHint")} {t("code.plan.adjustHint")}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={busy} aria-describedby={hintId} onClick={() => void answer(true)}>
              {t("code.plan.approve")}
            </Button>
            <Button size="sm" variant="outline" disabled={busy} aria-describedby={hintId} onClick={() => void answer(false)}>
              {t("code.plan.adjust")}
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
