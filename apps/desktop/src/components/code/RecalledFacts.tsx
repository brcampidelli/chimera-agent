import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { deleteMemory } from "@/lib/api";
import type { RecalledFact } from "@/lib/api";
import { Badge } from "@/components/ui/panel";
import { focusRing } from "@/components/ui/focus";
import { memorySourceLabel } from "@/lib/memorySource";
import type { TFunc } from "@/lib/i18n";

const forgetBtn = `rounded-chip text-xs text-accent-ink hover:underline ${focusRing}`;

/** The facts a turn drew on, listed — not only counted.
 *
 * The receipt used to say "3 memories used this turn" and nothing else, so a wrong or stale fact
 * steering an answer could be neither found nor removed from where it was noticed. Each row carries
 * where the fact came from and, when it was learned from untrusted content, the same unverified
 * label the model was shown. "Forget" calls the Memory screen's own delete route, so the fact is
 * gone from the store and cannot be recalled by the next turn.
 *
 * A closed `<details>` rather than a floating popover: it needs no positioning, it is reachable by
 * keyboard, and the summary is the same chip the receipt always drew.
 */
export function RecalledFacts({
  facts,
  label,
  title,
  t,
}: {
  facts: readonly RecalledFact[];
  /** The chip's text ("3 memories used this turn"). */
  label: string;
  title: string;
  t: TFunc;
}) {
  const qc = useQueryClient();
  const [gone, setGone] = useState<ReadonlySet<string>>(new Set());
  const [failed, setFailed] = useState<string | null>(null);
  const shown = facts.filter((f) => !gone.has(f.id));

  async function forget(id: string) {
    setFailed(null);
    try {
      await deleteMemory(id);
    } catch {
      // The row stays: a fact that is still stored must not look forgotten.
      setFailed(id);
      return;
    }
    setGone((prev) => new Set(prev).add(id));
    void qc.invalidateQueries({ queryKey: ["memory"] });
    void qc.invalidateQueries({ queryKey: ["memory-layers"] });
  }

  return (
    <details className="open:basis-full">
      <summary className={`cursor-pointer list-none rounded-chip ${focusRing}`}>
        <Badge title={title}>{label}</Badge>
      </summary>
      <ul aria-label={t("code.chat.recalledList")} className="mt-1.5 flex flex-col gap-1.5">
        {shown.map((fact) => (
          <li key={fact.id} className="surface flex items-start gap-2 px-3 py-2 text-xs">
            <div className="min-w-0 flex-1">
              <p className="break-words text-foreground">{fact.content}</p>
              <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-muted-foreground">
                {fact.source ? (
                  <span>{t("code.chat.recalledFrom", { source: memorySourceLabel(t, fact.source) })}</span>
                ) : null}
                {fact.unverified ? <Badge tone="warn">{t("memory.unverified")}</Badge> : null}
                {failed === fact.id ? <span role="alert">{t("code.chat.recalledForgetFailed")}</span> : null}
              </p>
            </div>
            <button type="button" className={forgetBtn} onClick={() => void forget(fact.id)}>
              {t("code.chat.recalledForget")}
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
