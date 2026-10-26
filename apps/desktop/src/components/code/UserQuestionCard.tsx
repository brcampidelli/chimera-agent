import { useEffect, useRef, useState } from "react";
import { answerUserQuestion, type UserQuestionEvent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/** An option that means "something else": choosing it asks for text, it is not an answer itself.
 *  The agent writes its own labels, so this reads the common ways of saying it in the app's
 *  languages ("Other (describe)", "Outra…", "Otro…", "Autre…", "Andere…", "その他", "其他"). */
const OTHER_OPTION =
  /^(?:other|outr[ao]s?|otr[ao]s?|autres?|andere[rsnm]?|altro|altri|inn[ey]|другое|иное|その他|其他|其它)(?![\p{L}\p{N}])/iu;

export const isOtherOption = (label: string): boolean => OTHER_OPTION.test(label.trim());

/** A user answer is ordinary input for the paused tool, never an approval or policy decision. */
export function UserQuestionCard({
  question,
  onAnswered,
  onSendAsNext,
  onOpenDesign,
}: {
  question: UserQuestionEvent;
  onAnswered: () => void;
  /** The owner's text for a question that expired, sent as the conversation's next message. */
  onSendAsNext?: (text: string) => void;
  onOpenDesign?: (id: string) => void;
}) {
  const t = useT();
  const [freeText, setFreeText] = useState("");
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [other, setOther] = useState(false);
  const [gone, setGone] = useState(question.expired === true);
  const inputRef = useRef<HTMLInputElement>(null);

  // The asking turn stops waiting `wait_seconds` after it asked. Past that, no answer reaches it:
  // the card says so instead of taking one more answer that nobody will read.
  useEffect(() => {
    if (gone || !question.wait_seconds) return;
    const left = question.asked_at * 1000 + question.wait_seconds * 1000 - Date.now();
    if (left <= 0) {
      setGone(true);
      return;
    }
    const timer = setTimeout(() => setGone(true), left);
    return () => clearTimeout(timer);
  }, [gone, question.asked_at, question.wait_seconds]);

  const submit = async (answer: string, allowEmpty = false) => {
    if ((!answer.trim() && !allowEmpty) || busy) return;
    setBusy(true);
    try {
      const response = await answerUserQuestion(question.id, answer);
      if (response.ok) onAnswered();
      // `ok: false` is the server saying the question is not waiting any more (it timed out, or
      // was answered elsewhere) — not a transient failure, so retrying would only fail again.
      else {
        setFreeText((text) => (text.trim() ? text : answer));
        setGone(true);
      }
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  const pick = (option: string) => {
    if (question.allow_free_text && isOtherOption(option)) {
      // "Other" is a request to write, not an answer: it sends nothing until there is text.
      setOther(true);
      inputRef.current?.focus();
      return;
    }
    void submit(question.option_details?.[option] ?? option);
  };

  const sendAsNext = () => {
    const text = freeText.trim();
    if (!text || !onSendAsNext) return;
    onSendAsNext(text);
  };

  return (
    <section data-card="user-question" className="rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold">{question.label || t("code.question.title")}</h3>
      {question.kind === "design_approval" && (
        <p className="mt-2 text-sm font-medium text-foreground">
          {question.title} — {t("design.approval.revision", { revision: question.revision ?? 0 })}
        </p>
      )}
      {question.description ? (
        <p className="mt-2 text-sm text-muted-foreground">{question.description}</p>
      ) : (
        <p className="mt-2 text-sm text-foreground">{question.question}</p>
      )}
      {gone ? (
        <div className="mt-3 flex flex-col gap-2">
          <p role="status" className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">{t("code.question.expired")}</span>
            {" — "}
            {t("code.question.expiredNote")}
          </p>
          <input
            className="min-w-0 rounded-md border border-border bg-background px-3 py-2 text-sm"
            aria-label={t("code.question.freeText")}
            value={freeText}
            onChange={(event) => setFreeText(event.target.value)}
          />
          <div>
            <Button type="button" disabled={!freeText.trim() || !onSendAsNext} onClick={sendAsNext}>
              {t("code.question.sendAsNext")}
            </Button>
          </div>
        </div>
      ) : (
        <>
          {question.kind === "design_approval" && question.document_id && (
            <div className="mt-3">
              <Button type="button" variant="outline" onClick={() => onOpenDesign?.(question.document_id!)}>
                {t("design.approval.open")}
              </Button>
            </div>
          )}
          {question.kind === "design_approval" ? (
            <div className="mt-3 flex flex-col gap-2">
              <input
                className="min-w-0 rounded-md border border-border bg-background px-3 py-2 text-sm"
                aria-label={t("design.approval.adjustmentNote")}
                placeholder={t("design.approval.adjustmentNote")}
                value={freeText}
                onChange={(event) => setFreeText(event.target.value)}
                disabled={busy}
              />
              <div className="flex flex-wrap gap-2">
                <Button type="button" disabled={busy} onClick={() => void submit("approved")}>
                  {t("design.approval.approve")}
                </Button>
                <Button type="button" variant="outline" disabled={busy}
                  onClick={() => void submit(`adjustments: ${freeText.trim()}`, true)}>
                  {t("design.approval.adjustments")}
                </Button>
              </div>
            </div>
          ) : (
            <>
              {question.options.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {question.options.map((option) => (
                    <Button
                      key={option}
                      disabled={busy}
                      title={question.option_details?.[option]}
                      aria-pressed={other && isOtherOption(option) ? true : undefined}
                      onClick={() => pick(option)}
                    >
                      {option}
                    </Button>
                  ))}
                </div>
              )}
              {question.allow_free_text && (
                <form
                  className="mt-3 flex gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void submit(freeText);
                  }}
                >
                  <input
                    ref={inputRef}
                    className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
                    aria-label={t("code.question.freeText")}
                    placeholder={other ? t("code.question.otherHint") : t("code.question.freeText")}
                    value={freeText}
                    onChange={(event) => setFreeText(event.target.value)}
                    disabled={busy}
                  />
                  <Button type="submit" disabled={busy || !freeText.trim()}>{t("code.question.submit")}</Button>
                </form>
              )}
            </>
          )}
        </>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-bad-foreground">{t("code.question.answerFailed")}</p>}
    </section>
  );
}
