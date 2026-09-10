import { useEffect, useState } from "react";
import { ShieldQuestion } from "lucide-react";

import { ApprovalCard } from "@/components/code/ApprovalCard";
import { Dialog } from "@/components/ui/dialog";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
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
          // `refetch` rather than a local removal: the list is the server's, and the card that was
          // just answered is not the only thing that may have changed since the last poll.
          <ApprovalCard key={q.id} question={q} onAnswered={() => void refetch()} />
        ))}
      </Dialog>
    </>
  );
}
