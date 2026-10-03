import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldQuestion } from "lucide-react";

import { ApprovalCard } from "@/components/code/ApprovalCard";
import { Dialog } from "@/components/ui/dialog";
import { focusRing } from "@/components/ui/focus";
import { listCodeSessions, type CodeSessionMeta } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";
import { NOTIFY_APPROVALS_KEY, notifyIfAway, useNotifyFlag } from "@/lib/notify";
import type { ApprovalQuestion } from "@/lib/types";
import { usePendingApprovals } from "@/lib/usePendingApprovals";
import { cn } from "@/lib/utils";

/**
 * A parked question, from every screen.
 *
 * Governance can stop a tool call and ask a person. The question is written durably and answered
 * over `POST /api/approvals/{id}` — and until this existed it was visible from exactly one screen:
 * Governance, inside the Security tab of Settings, which polls `GET /api/approvals` and draws an
 * `ApprovalCard` per question. Everywhere else showed nothing. The conversation holds its own
 * question in local state, and the stream frame that puts it there reaches only the window that
 * started the turn.
 *
 * That is not cosmetic, because silence is not neutral: an unanswered question is refused after
 * `WAIT_SECONDS` (`chimera/governance/pending.py`), so a question nobody notices is a refusal
 * nobody decided. Measured on the injection bench, the difference between the two readings the
 * Security screen prints side by side — "over-block" with nobody to ask against "with approver" —
 * is the difference between a person seeing this and not.
 *
 * **Why the status bar.** The bar's own docstring already makes this argument about a different
 * subject: the agent used to vanish when you left the chat, and Stop was reachable only from the
 * composer, "so navigating away mid-run stranded you with a running agent and no way to halt it".
 * A question reachable only from Governance is the same defect one surface later, and this bar is
 * where the app already puts the thing you might have walked away from. The two alternatives were
 * both closed by rules the repo had already written down: `IconRail.test.tsx` caps the rail at five
 * destinations and says "the ceiling is now spent", and `ui/toast.tsx` says a toast is
 * "deliberately not for errors that need a decision… anything the user must act on belongs in the
 * surface that owns it".
 *
 * **Why a dialog rather than a link to Governance.** The question comes to the person instead of
 * sending them to Settings › Security to find it, and the body is `ApprovalCard` itself — the same
 * component, so `answerApproval(id, approved)` stays the one and only answering path, the reason
 * line stays the ledger's own sentence, and a stale click keeps behaving the way it already does.
 *
 * **What the card cannot show from here, and why it stays dark.** `ApprovalCard` counts its
 * deadline down and at zero drops the buttons and says silence answered (#416) — from
 * `wait_seconds`, which `ApprovalOut` does not carry. A question read off `GET /api/approvals`
 * knows when it was ASKED and not how long its turn will wait, so the card falls back to showing no
 * deadline line, which is its own documented answer to a number it does not have. Deriving one from
 * `WAIT_SECONDS` would be arithmetic on a default that `ask_durably` takes an override for,
 * presented as a fact, on the surface whose whole job is not doing that. The fix is a field on
 * `ApprovalOut`; it is not this component inventing one.
 *
 * What DOES stay honest without it is disappearance: the waiting thread deletes the request file
 * once the question is answered or refused, so the poll below is what retires a question — and it
 * retires it here and on Governance at the same moment, because both read one query.
 *
 * **Nothing at zero.** Not an empty chip, not a `0`. An indicator that is always on screen is one
 * people learn to stop seeing, and this one has to be worth looking at the ten minutes a year it
 * appears.
 *
 * One thing it does NOT do: announce itself. A live region has to be in the DOM before its content
 * arrives or the change goes unread (`ui/toast.tsx` learned this), and a region that is always
 * present is the empty chip this component refuses to render. So the chip is silent until a
 * keyboard user reaches it; the sentence it carries when they do is the whole question count.
 */
export function PendingApprovals() {
  const t = useT();
  const [open, setOpen] = useState(false);
  // The one caller that owns the timer. This component is mounted on every screen — including the
  // one Governance renders inside — so its poll is the only one the app needs.
  const { data, refetch } = usePendingApprovals({ poll: true });
  const questions = data ?? [];
  const n = questions.length;

  // A question answered from the CLI, from another window, or by the silence that refuses it stops
  // coming back from the poll. Closing on the way to zero matters because the dialog would
  // otherwise be re-opened by the next question that arrives, with a person who never asked for it
  // looking at a decision they have not read.
  useEffect(() => {
    if (n === 0) setOpen(false);
  }, [n]);

  useApprovalNotice(data);

  if (n === 0) return null;

  const label = t("approvals.waiting", { n });
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        // The visible text is the count alone — this bar is eight pixels of type and a sentence
        // here would crowd out the run it sits beside. The name carries the sentence, and contains
        // the visible digits, so a voice user can still say what they see.
        aria-label={label}
        title={label}
        className={cn(
          "flex items-center gap-1.5 rounded-chip px-2 py-0.5 font-medium",
          // The same tone `Badge` gives `accent`, which is what the Security screen already uses
          // for anything the trust kernel has an opinion about.
          "bg-accent/15 text-accent-ink ring-1 ring-accent/25",
          "transition-colors duration-1 ease-out hover:bg-accent/25",
          focusRing,
        )}
      >
        <ShieldQuestion className="h-3 w-3" aria-hidden />
        {n}
      </button>
      <Dialog open={open} onOpenChange={setOpen} title={t("code.approval.title")}>
        {questions.map((q) => (
          <div key={q.id} className="space-y-1">
            <ApprovalOrigin question={q} />
            {/* `refetch` rather than a local removal: the list is the server's, and the card that
                was just answered is not the only thing that may have changed since the last poll. */}
            <ApprovalCard question={q} onAnswered={() => void refetch()} />
          </div>
        ))}
      </Dialog>
    </>
  );
}

/**
 * A desktop notification when a new question arrives while the window has no focus — opt-in, in
 * Settings › General › Notifications.
 *
 * The chip above is only seen by someone looking at the app, and the question it carries is refused
 * by silence after `WAIT_SECONDS`. So the one case worth an OS notification is exactly the one the
 * chip cannot cover: the person is in another window.
 *
 * "New" is a question id this poll returns that the previous one did not, rather than the count
 * going up: an answer and a new question landing between two polls leave the count where it was
 * and are still a new question. The first answer of the poll is the baseline, so opening the app
 * never announces what was already waiting.
 *
 * The notification names WHERE the question comes from and nothing else. Never the command: the
 * action is the tool call governance stopped, possibly written by a model that read a web page, and
 * an OS notification is outside every boundary this app draws. The project is a folder name and the
 * conversation's title is the person's own first message. Never actionable — answering happens in
 * the card, where the reason and the ledger are.
 */
function useApprovalNotice(questions: ApprovalQuestion[] | undefined) {
  const t = useT();
  const [on] = useNotifyFlag(NOTIFY_APPROVALS_KEY);
  // Only fetched when the option is on. The arrow is deliberate: it reads `listCodeSessions` when
  // the query runs, not when this renders.
  const sessions = useQuery({
    queryKey: ["code-sessions"],
    queryFn: () => listCodeSessions(),
    staleTime: 30_000,
    enabled: on,
  });
  const known = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!questions) return;
    const before = known.current;
    known.current = new Set(questions.map((q) => q.id));
    if (before === null || !on) return;
    const fresh = questions.find((q) => !before.has(q.id));
    if (!fresh) return;
    void notifyIfAway(t("notify.approval.title"), approvalNoticeBody(fresh, sessions.data, t));
  }, [questions, on, sessions.data, t]);
}

/** Where a question comes from, for a notification: the same fields `ApprovalOrigin` reads, minus
 *  the background work's title, which is not the person's own words. */
function approvalNoticeBody(
  question: ApprovalQuestion,
  sessions: CodeSessionMeta[] | undefined,
  t: TFunc,
): string {
  if (!question.session_id && !question.workspace) return t("notify.approval.body");
  const project = question.workspace ? projectName(question.workspace) : t("approvals.defaultProject");
  if (question.work) return t("notify.approval.fromWork", { project });
  const title = sessions?.find((s) => s.id === question.session_id)?.title;
  const conversation = (title || question.session_id.slice(0, 8)).slice(0, 60);
  return t("approvals.from", { project, conversation });
}

/** The last part of a folder path, which is how the sidebar names a project. */
function projectName(workspace: string): string {
  return workspace.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? workspace;
}

/**
 * Which project and which conversation a question comes from.
 *
 * The list above shows every conversation's questions in one place. With several working at once, a
 * card that said only "run_shell: rm -rf build" could be answered for the wrong project, so each says
 * where it comes from. The conversation's title is read from the same list the sidebar keeps (one
 * query key, no new request). Nothing is drawn for a question whose origin the server does not know.
 */
function ApprovalOrigin({ question }: { question: ApprovalQuestion }) {
  const t = useT();
  const sessions = useQuery({ queryKey: ["code-sessions"], queryFn: listCodeSessions, staleTime: 30_000 });
  if (!question.session_id && !question.workspace) return null;
  const project = question.workspace ? projectName(question.workspace) : t("approvals.defaultProject");
  const title = sessions.data?.find((s) => s.id === question.session_id)?.title;
  const text = question.work
    ? t("approvals.fromWork", { project, work: question.work })
    : t("approvals.from", { project, conversation: title || question.session_id.slice(0, 8) });
  return <p className="text-xs text-muted-foreground">{text}</p>;
}
