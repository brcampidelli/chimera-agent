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
  /** The verdict's level — `review`, `warn`, or `block`.
   *
   * It has always been on the wire (`ApprovalOut`, and the stream frame `code_api` emits) and the
   * CLI table has always printed it; this card was the one surface that dropped it. The backend
   * *sorts the queue* by it (`pending.LEVEL_RANK`, from arXiv 2608.06949: ordering an overloaded
   * answerer's queue by risk recovered coverage from 65.6% to 91.7%), so the person answering was
   * being handed a risk-ordered queue with the risk removed.
   *
   * Optional because a question replayed from an older run may not carry one, and an absent level
   * is shown as nothing rather than guessed into `review`. */
  decision?: string;
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

/** The three levels a verdict can carry, and the tone each is drawn in.
 *
 * Closed set: it mirrors `chimera/governance/pending.py::LEVEL_RANK` minus `allow`, which never
 * raises a question. `block` is here for completeness of the wire shape — in practice a BLOCK
 * returns its refusal before any approver is consulted (`governed_tool.py`), so it should never
 * reach this card; drawing it as `bad` rather than omitting it means that if one ever does, it
 * arrives looking like what it is instead of silently as an ordinary review. */
const LEVEL_LABEL: Record<string, { key: string; tone: string } | undefined> = {
  block: { key: "code.approval.level.block", tone: "border-bad/40 text-bad-foreground" },
  review: { key: "code.approval.level.review", tone: "border-accent/40 text-accent-foreground" },
  warn: { key: "code.approval.level.warn", tone: "border-warn/40 text-warn-foreground" },
};

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
  // Normalised here and nowhere else. A level the UI has no word for is dropped rather than shown
  // raw or coerced into `review`: an unrecognised string on a risk chip is worse than no chip.
  //
  // A literal map rather than `` t(`code.approval.level.${raw}`) ``, and that is deliberate:
  // `i18n.reachable.test` proves every key in ten languages is rendered somewhere by searching the
  // source for the key as a string, and an interpolated key is invisible to it. The test keeps a
  // `DYNAMIC` prefix escape hatch, which belongs to keys whose suffix comes from open data — these
  // three are a closed set fixed by `pending.LEVEL_RANK`, so spelling them out keeps the guard
  // working instead of buying an exemption from it.
  const level = LEVEL_LABEL[(question.decision ?? "").trim().toLowerCase()];
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
        {/* The level, at the top, beside the title: it is what the backend sorted this queue by,
            and the answerer could not see it. Unknown levels render nothing rather than a guess. */}
        {level ? (
          <span className={cn("rounded-full border px-1.5 py-0.5 text-xs font-medium", level.tone)}>
            {t(level.key)}
          </span>
        ) : null}
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
      {/* REFUSE FIRST, and it is the primary button. Not a style preference — the other two
          surfaces already carry this posture and wrote down why. The TUI declares
          `AUTO_FOCUS = "#ask-no"` with the note "focus lands on NO, so a person who answers by
          hitting Enter without reading has refused"; the REPL uses `typer.confirm(default=False)`
          and prints `[y/N]`. This card shipped the exact inverse: "Allow this once" rendered first
          and styled primary, so inside `PendingApprovals`'s Radix dialog the focus trap put the
          caret on ALLOW — the fail-safe, running backwards, on the one surface where arXiv
          2606.05647 measured people clicking through (of 16 sessions where the monitor alerted
          CORRECTLY, 9 approved the malicious change anyway, 67% of those after minimal review).

          Order, not `autoFocus`. This card also mounts inline in the composer, where stealing focus
          would yank the caret out of a half-typed message; DOM order gives the dialog its safe
          default and costs the inline mount nothing. */}
      {expired ? null : (
        <div className="mt-2 flex gap-2">
          <Button size="sm" disabled={busy} onClick={() => void answer(false)}>
            {t("code.approval.refuse")}
          </Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void answer(true)}>
            {t("code.approval.approve")}
          </Button>
        </div>
      )}
    </div>
  );
}
