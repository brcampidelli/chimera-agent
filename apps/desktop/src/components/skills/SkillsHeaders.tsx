import type { UseQueryResult } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/async";
import { SkillsActiveNow } from "@/components/SkillsActiveNow";
import { useT } from "@/lib/i18n";
import type { CatalogEntry, LibraryCard, PinnedCatalog, SkillsResponse } from "@/lib/types";
import { RATINGS, type Grouping, type Rating } from "@/components/skills/skillItems";

/**
 * Above Mine: what a run started now is actually told — the answer the lists below only add up to —
 * then what waits for a decision, then why every learned count may read zero.
 */
export function MineHeader({
  skills,
  learnedEmpty,
  onReview,
}: {
  skills: UseQueryResult<SkillsResponse>;
  /** Learned cards are none while Mine still lists something else: said here, since that empty
   *  section is not drawn. */
  learnedEmpty: boolean;
  onReview: () => void;
}) {
  const t = useT();
  const stats = skills.data?.stats ?? [];
  const pending = stats.filter((s) => s.status === "pending").length;
  return (
    <div className="space-y-4">
      <SkillsActiveNow />
      {skills.isError ? <ErrorState error={skills.error} onRetry={() => void skills.refetch()} /> : null}
      {pending > 0 ? (
        <div className="flex items-center gap-3 rounded-xl2 border border-warn/40 bg-warn/10 px-4 py-2.5">
          <span className="flex-1 text-sm text-warn-foreground">{t("skills.pendingNotice", { n: pending })}</span>
          <Button size="sm" variant="outline" onClick={onReview}>
            {t("skills.review")}
          </Button>
        </div>
      ) : null}
      {/* Why every count may be zero. Without this the list reads as "the agent tried these and
          they did not help", when nothing consulted them: retrieval is off by default because it
          was measured and did not earn its tokens. Quiet when reading is on — then zero is a
          verdict, and a permanent disclaimer would explain it away. */}
      {skills.data?.cards_read === false && stats.length > 0 ? (
        <p className="text-xs text-muted-foreground">{t("skills.notRead")}</p>
      ) : null}
      {learnedEmpty && skills.isSuccess ? <p className="text-xs text-muted-foreground">{t("skills.empty")}</p> : null}
    </div>
  );
}

/**
 * Above Discover: where each group comes from and on whose terms — once, not on every card — and
 * the two choices the shared shell has no control for: whether a skill works here, and whether the
 * third-party catalogue is grouped by topic or listed by name.
 */
export function DiscoverHeader({
  library,
  catalog,
  pinned,
  fit,
  grouping,
  onFit,
  onGrouping,
}: {
  library: UseQueryResult<LibraryCard[]>;
  catalog: UseQueryResult<CatalogEntry[]>;
  pinned: UseQueryResult<PinnedCatalog>;
  fit: Rating | null;
  grouping: Grouping;
  onFit: (next: Rating | null) => void;
  onGrouping: (next: Grouping) => void;
}) {
  const t = useT();
  const entries = catalog.data ?? [];
  const pin = pinned.data;
  return (
    <div className="space-y-3">
      <dl className="space-y-1.5 text-xs text-muted-foreground">
        <div>
          <dt className="inline font-semibold text-foreground">{t("skills.library")}</dt>
          {" · "}
          <dd className="inline">{t("skills.libraryBlurb")}</dd>
        </div>
        <div>
          <dt className="inline font-semibold text-foreground">{t("catalog.title")}</dt>
          {" · "}
          <dd className="inline">{t("catalog.provenance")}</dd>{" "}
          <dd className="inline">{t("catalog.subtitle", { n: entries.length, installed: entries.filter((e) => e.installed).length })}</dd>
        </div>
        {pin ? (
          <div>
            <dt className="inline font-semibold text-foreground">{t("skills.discover.pinned")}</dt>
            {" · "}
            <dd className="inline">
              {pin.available
                ? t("skills.discover.pinnedSource", { repo: pin.repo, sha: pin.sha.slice(0, 7) })
                : t("skills.discover.pinnedUnavailable", { reason: pin.reason ?? "—" })}
            </dd>
          </div>
        ) : null}
      </dl>
      {library.isError ? <ErrorState error={library.error} onRetry={() => void library.refetch()} /> : null}
      {/* Not "you have none yet": these are shipped, never earned, so an empty list means this
          build lost them rather than that the owner has not done anything. */}
      {library.isSuccess && library.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("skills.libraryEmpty")}</p>
      ) : null}
      {catalog.isError ? <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} /> : null}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <label className="flex items-center gap-1">
          {t("catalog.filter.portability")}
          <select
            className="field h-7 px-2 text-xs"
            value={fit ?? ""}
            onChange={(e) => onFit((RATINGS as readonly string[]).includes(e.target.value) ? (e.target.value as Rating) : null)}
          >
            <option value="">{t("catalog.filter.all")}</option>
            {RATINGS.map((r) => (
              <option key={r} value={r}>
                {t(`catalog.portability.${r}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1">
          {t("catalog.sort")}
          <select
            className="field h-7 px-2 text-xs"
            value={grouping}
            onChange={(e) => onGrouping(e.target.value === "name" ? "name" : "topic")}
          >
            <option value="topic">{t("catalog.sort.topic")}</option>
            <option value="name">{t("catalog.sort.name")}</option>
          </select>
        </label>
      </div>
    </div>
  );
}
