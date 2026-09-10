import { useEffect, useState } from "react";
import { ShieldQuestion, ShieldX } from "lucide-react";
import { answerApproval } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
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
 *
 * **The deadline is counted, not announced once.** The sentence used to be computed from
 * `wait_seconds` at render and then never touched again, so a card that had already expired went on
 * offering two live-looking buttons over the words "silence refuses after 300s" — the number was
 * true when it was printed and a lie for every second after. Pressing one of those buttons did
 * nothing a person could see: the server refused the call minutes earlier, `answerApproval` answers
 * `ok: false`, and the card disappears exactly as it does on a real answer. That is the failure this
 * whole surface exists to prevent, wearing the interface that was supposed to prevent it — the
 * measured shape of it is `chimera tui`, where a `run_shell` blocked 123.8 s against a 120 s timeout
 * and came back as `✗ run_shell` with no explanation. So the card counts down, and at zero it stops
 * asking and says what silence did.
 */
/** What the card needs of a question. The stream frame carries `wait_seconds`; a question listed
 *  from `GET /api/approvals` after a reload does not know how long its turn will wait, and the card
 *  says nothing rather than a number it does not have. */
export interface ApprovalQuestionLike {
  id: string;
  action: string;
  reason: string;
  wait_seconds?: number;
  /** When the call parked, in the SERVER's epoch seconds. Both wire shapes carry it — the stream
   *  frame (`chimera/api/code_api.py`) and `ApprovalOut` — and it is what lets a card that mounts
   *  LATE show the time that is actually left rather than the time the question started with. */
  asked_at?: number;
}

/**
 * Seconds before silence answers, ticking, or `null` for a question with no deadline on the wire.
 *
 * Two anchors, and which one is used is the whole of the honesty here. `asked_at` is a *server*
 * epoch (`pending.ask_durably` writes `time.time()`) and the browser's clock is not necessarily the
 * server's — it is the same machine for `chimera app`, and is not for a remote Chimera
 * (`lib/server.ts`). The two branches are therefore not symmetric, and each has its own reason:
 *
 *   - **The timestamp implies time already spent.** Honoured, clamped to the window. This is the
 *     case that matters — a frame REPLAYED after a dropped connection (`resumeCodeTurn`) carries the
 *     original `asked_at`, so a card anchored on arrival would offer a fresh 300 s for a question
 *     that has eleven left, or none. Past the window it reads as already refused, which is the
 *     explanation the turn otherwise never gets.
 *   - **The timestamp implies the future.** Distrusted entirely, and the card counts from the moment
 *     it was handed the question instead. A question cannot be asked later than now in the server's
 *     own clock, so a negative elapsed is not a fact about the question — it is proof that these are
 *     two different clocks, and the arrival anchor is the one that needs neither of them. It is also
 *     exact for the ordinary case, since the server emits the frame as the call parks.
 */
function useSecondsLeft(question: ApprovalQuestionLike): number | null {
  const { id, wait_seconds: wait, asked_at: askedAt } = question;
  const [left, setLeft] = useState<number | null>(null);

  useEffect(() => {
    if (wait === undefined) {
      setLeft(null);
      return;
    }
    const implied = askedAt === undefined ? 0 : Date.now() / 1000 - askedAt;
    const spent = implied < 0 ? 0 : Math.min(implied, wait);
    const deadline = Date.now() + (wait - spent) * 1000;
    // Ceil, so the last whole second on screen is 1 and not 0: a "0s" that still offers buttons
    // reads as the contradiction this component was written to remove.
    const tick = () => setLeft(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
    // `id` is in the list because the same mounted card is reused for the NEXT question of a turn,
    // and a countdown that kept the previous question's deadline would be counting the wrong pause.
  }, [id, wait, askedAt]);

  return left;
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
  const left = useSecondsLeft(question);
  const expired = left === 0;
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
      className={cn(
        "mb-2 rounded-xl border px-3 py-2 text-sm",
        expired ? "border-bad/40 bg-bad/5" : "border-accent/40 bg-accent/5",
      )}
    >
      <div className="flex items-center gap-2 font-medium">
        {expired ? (
          <ShieldX className="h-4 w-4 text-bad-foreground" aria-hidden="true" />
        ) : (
          <ShieldQuestion className="h-4 w-4 text-accent-foreground" aria-hidden="true" />
        )}
        {t("code.approval.title")}
      </div>
      <p className="mt-1 text-muted-foreground">{question.reason}</p>
      {question.action ? (
        <p className="mt-1 font-mono text-xs text-muted-foreground">{question.action}</p>
      ) : null}
      {/* The deadline line, in its two states. `aria-live` because the number changes without the
          reader touching anything, and the change from "refuses in 4s" to "silence refused this" is
          the one moment on this card where nothing on screen was pressed and everything meant by it
          changed. `polite`, not `assertive`: it must not interrupt someone mid-sentence over a
          countdown that has 300 seconds to run. */}
      {left !== null ? (
        <p
          aria-live="polite"
          className={cn("mt-1 text-xs", expired ? "text-bad-foreground" : "text-muted-foreground")}
        >
          {expired ? t("code.approval.expired") : t("code.approval.expires", { s: left })}
        </p>
      ) : null}
      {/* Gone rather than disabled once silence has answered. A disabled button says "not now";
          these two are not coming back, and the tool call they belonged to was refused. */}
      {expired ? null : (
        <div className="mt-2 flex gap-2">
          <Button size="sm" disabled={busy} onClick={() => void answer(true)}>
            {t("code.approval.approve")}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void answer(false)}>
            {t("code.approval.refuse")}
          </Button>
        </div>
      )}
    </div>
  );
}
