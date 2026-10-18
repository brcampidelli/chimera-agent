import { useQuery } from "@tanstack/react-query";

import { ErrorState } from "@/components/ui/async";
import { Badge, EmptyState, Panel, Spinner } from "@/components/ui/panel";
import { getMemoryLayers, getMemoryProfile } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { memorySourceLabel } from "@/lib/memorySource";

/**
 * What the agent has learned about you.
 *
 * `GET /api/memory/profile` existed and no screen read it, while Memory happily wrote persona facts
 * into the same store — so you could feed a picture of yourself that you were never shown. For a
 * feature whose entire premise is "it remembers you", not being able to see what it remembers is
 * the part that matters most.
 *
 * ONE list. The route sends the persona facts twice: `profile` is the ranked block the agent gets in
 * its prompt (each fact learned from untrusted content carries an English label addressed to the
 * MODEL), and `persona` is the same rows as records. The screen used to draw both, under two
 * headings, so every fact appeared twice and the model's label leaked onto a translated screen. The
 * records are what a person needs — the text, where it came from, whether it is verified — so they
 * are all this renders; `profile` stays on the wire for the readers it was written for.
 *
 * Read-only by design: the profile is derived by the agent from persona facts, so the honest place
 * to change it is the facts themselves, in Memory.
 */
export function Profile({ onOpenMemory }: { onOpenMemory?: () => void } = {}) {
  const t = useT();
  const profile = useQuery({ queryKey: ["memory-profile"], queryFn: getMemoryProfile });
  // Only to tell "nothing stored at all" from "nothing stored about YOU": the two need different
  // advice, and the second is the one where the facts already exist in Memory under another kind.
  const layers = useQuery({ queryKey: ["memory-layers"], queryFn: getMemoryLayers });

  if (profile.isLoading) return <Spinner />;
  if (profile.isError)
    return <ErrorState error={profile.error} onRetry={() => profile.refetch()} />;

  const persona = profile.data?.persona ?? [];
  const otherFactsExist = (layers.data?.total ?? 0) > 0;

  return (
    <Panel
      title={t("profile.summary")}
      // Read-only here on purpose (see above), so the way to change a fact is one click away
      // rather than a tab the reader has to know about.
      action={
        onOpenMemory ? (
          <button type="button" onClick={onOpenMemory} className="text-xs text-accent-ink hover:underline">
            {t("profile.manage")}
          </button>
        ) : undefined
      }
    >
      {persona.length === 0 ? (
        <EmptyState text={otherFactsExist ? t("profile.noFacts") : t("profile.empty")} />
      ) : (
        <ul aria-label={t("profile.facts")} className="divide-y divide-hairline">
          {persona.map((item) => (
            <li key={item.id} className="px-4 py-2.5">
              <p className="whitespace-pre-line text-sm">{item.content}</p>
              {/* Where the fact came from. A persona fact the agent inferred and one you typed
                  yourself carry different weight, and you should be able to tell them apart. */}
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <span>{memorySourceLabel(t, item.source)}</span>
                {/* The same word and colour Memory uses for it, instead of the raw "tainted". */}
                {item.provenance === "tainted" ? <Badge tone="warn">{t("memory.unverified")}</Badge> : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
