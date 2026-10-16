import { useState } from "react";
import { answerUserQuestion, type UserQuestionEvent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/** A user answer is ordinary input for the paused tool, never an approval or policy decision. */
export function UserQuestionCard({
  question,
  onAnswered,
}: {
  question: UserQuestionEvent;
  onAnswered: () => void;
}) {
  const t = useT();
  const [freeText, setFreeText] = useState("");
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);

  const submit = async (answer: string) => {
    if (!answer.trim() || busy) return;
    setBusy(true);
    try {
      const response = await answerUserQuestion(question.id, answer);
      if (response.ok) onAnswered();
      else setError(true);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-card="user-question" className="rounded-lg border border-border bg-card p-4">
      <h3 className="text-sm font-semibold">{t("code.question.title")}</h3>
      <p className="mt-2 text-sm text-foreground">{question.question}</p>
      {question.options.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          {question.options.map((option) => (
            <Button key={option} disabled={busy} onClick={() => void submit(option)}>{option}</Button>
          ))}
        </div>
      )}
      {question.allow_free_text && (
        <form className="mt-3 flex gap-2" onSubmit={(event) => { event.preventDefault(); void submit(freeText); }}>
          <input
            className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
            aria-label={t("code.question.freeText")}
            placeholder={t("code.question.freeText")}
            value={freeText}
            onChange={(event) => setFreeText(event.target.value)}
            disabled={busy}
          />
          <Button type="submit" disabled={busy || !freeText.trim()}>{t("code.question.submit")}</Button>
        </form>
      )}
      {question.expired && (
        <p role="status" className="mt-2 text-sm text-muted-foreground">{t("code.question.expired")}</p>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-bad-foreground">{t("code.question.answerFailed")}</p>}
    </section>
  );
}
