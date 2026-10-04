import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getEffectiveSkills } from "@/lib/api";
import { Panel, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { useT } from "@/lib/i18n";

/** The first seven characters of a commit, the length people recognise from every git screen. */
export function shortSha(ref: string | null | undefined): string {
  const value = ref ?? "";
  return /^[0-9a-f]{40}$/i.test(value) ? value.slice(0, 7) : value;
}

/** The calendar day of an ISO timestamp — what a person compares, without a timezone to misread. */
export function day(iso: string | null | undefined): string {
  return (iso ?? "").slice(0, 10);
}

/**
 * What a run started now is told about skills — the top of the Skills screen.
 *
 * "Mine" was spread over three panels below (learned cards, the library, the catalogue) and none of
 * them answered the question someone has when a run behaves oddly: what did the agent actually get?
 * The text shown here is the prompt's own (`GET /api/skills/effective`, built by the function the
 * agent calls), shown verbatim rather than rendered, for the reason the library dialog gives: a
 * prettier document than the one the model reads is the wrong thing to look at.
 *
 * Writing the test behind this panel is what found that, until it shipped, no switched-on bundle
 * had ever reached a prompt. A list of "on" switches is a claim; this is the evidence.
 */
export function SkillsActiveNow() {
  const t = useT();
  const [showText, setShowText] = useState(false);
  const effective = useQuery({ queryKey: ["skills-effective"], queryFn: getEffectiveSkills });

  const data = effective.data;
  const bundles = data?.bundles ?? [];

  return (
    <Panel title={t("skills.active.title")}>
      <div className="space-y-2 px-4 py-3 text-xs text-muted-foreground">
        {effective.isError ? (
          <ErrorState error={effective.error} onRetry={() => void effective.refetch()} />
        ) : effective.isLoading || !data ? (
          <Spinner />
        ) : (
          <>
            <p>{t("skills.active.blurb")}</p>
            {/* This screen belongs to no project, so it shows the whole home. Said, because a run
                in a folder whose pack the owner accepted gets a narrower list, and a run is also
                handed the built-in skills its task matches — neither of which is this list. */}
            <p>{t("skills.active.scope")}</p>
            {/* Switches thrown while no switched-on skill reached any prompt. Fixing that would
                have sent them all at once without anyone deciding it, so they wait for a new
                switch — and this is where someone looking for "what does the agent get" sees why
                one they remember turning on is missing. */}
            {(data.reconfirm ?? []).length > 0 ? (
              <p className="text-warn-foreground">
                {t("skills.active.reconfirm", { names: (data.reconfirm ?? []).join(", ") })}
              </p>
            ) : null}
            {bundles.length === 0 ? (
              <p>{t("skills.active.none")}</p>
            ) : (
              <ul className="space-y-1">
                {bundles.map((b) => (
                  <li key={b.name} className="flex flex-wrap items-baseline gap-2">
                    <span className="font-mono text-sm text-foreground">{b.name}</span>
                    {b.ref ? (
                      <span className="font-mono">
                        {t("skills.active.commit", { sha: shortSha(b.ref) })}
                        {b.committed_at ? ` · ${day(b.committed_at)}` : ""}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
            {/* Cards are chosen per task, so the honest answer is the rule and the pool, not a
                list that would claim a run reads all of them. Off — the measured default — says
                that nothing is read, so an "active" card with zero uses stops looking broken. */}
            <p>
              {data.cards_read
                ? t("skills.active.cardsOn", { k: data.cards_k ?? 0, n: (data.cards ?? []).length })
                : t("skills.active.cardsOff")}
            </p>
            {data.bundle_text ? (
              <>
                <button
                  type="button"
                  className="text-accent hover:underline"
                  aria-expanded={showText}
                  onClick={() => setShowText((open) => !open)}
                >
                  {t("skills.active.showText")}
                </button>
                {showText ? (
                  <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap wrap-break-word rounded-card bg-surface-2 p-2 font-mono text-xs text-muted-foreground">
                    {data.bundle_text}
                  </pre>
                ) : null}
              </>
            ) : null}
          </>
        )}
      </div>
    </Panel>
  );
}
