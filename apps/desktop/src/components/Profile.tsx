import { useQuery } from "@tanstack/react-query";

import { ErrorState } from "@/components/ui/async";
import { EmptyState, Panel, Spinner } from "@/components/ui/panel";
import { getMemoryProfile } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * What the agent has learned about you.
 *
 * `GET /api/memory/profile` existed and no screen read it, while Memory happily wrote persona facts
 * into the same store — so you could feed a picture of yourself that you were never shown. For a
 * feature whose entire premise is "it remembers you", not being able to see what it remembers is
 * the part that matters most.
 *
 * Read-only by design: the profile is derived by the agent from persona facts, so the honest place
 * to change it is the facts themselves, in Memory.
 */
export function Profile() {
  const t = useT();
  const profile = useQuery({ queryKey: ["memory-profile"], queryFn: getMemoryProfile });

  if (profile.isLoading) return <Spinner />;
  if (profile.isError)
    return <ErrorState error={profile.error} onRetry={() => profile.refetch()} />;

  const text = profile.data?.profile?.trim() ?? "";
  const persona = profile.data?.persona ?? [];

  return (
    <div className="space-y-6">
      <Panel title={t("profile.summary")}>
        <div className="px-4 py-3">
          {text ? (
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{text}</p>
          ) : (
            <p className="text-sm text-muted-foreground">{t("profile.empty")}</p>
          )}
        </div>
      </Panel>

      <Panel title={t("profile.facts")}>
        {persona.length === 0 ? (
          <EmptyState text={t("profile.noFacts")} />
        ) : (
          persona.map((item) => (
            <div key={item.id} className="px-4 py-2.5">
              <p className="text-sm">{item.content}</p>
              {/* Where the fact came from. A persona fact the agent inferred and one you typed
                  yourself carry different weight, and you should be able to tell them apart. */}
              <p className="mt-0.5 text-xs text-muted-foreground">
                {item.source}
                {item.provenance !== "clean" && ` · ${item.provenance}`}
              </p>
            </div>
          ))
        )}
      </Panel>
    </div>
  );
}
