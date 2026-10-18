import { useQuery } from "@tanstack/react-query";

import { getEffectiveSkills, getSkillLibraryCard } from "@/lib/api";
import { ErrorState } from "@/components/ui/async";
import { Badge, Spinner } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { CatalogEntry, LibraryCard, PinnedCatalog, PinnedCatalogItem, SkillBundle, SkillStat } from "@/lib/types";
import { Portability, PortabilityReason, Requires } from "@/components/skills/SkillCardNote";
import {
  Commit,
  Fact,
  Facts,
  LicenceChip,
  PRE,
  Reaches,
  ReadText,
  SourceLink,
  Uninstall,
} from "@/components/skills/SkillDetailParts";
import { LearnedBadges, UpdateCheck } from "@/components/skills/SkillParts";
import { isPermissive, STAGES } from "@/components/skills/skillItems";

export function EntryBody({
  entry,
  bundle,
  onRead,
  onRemoved,
}: {
  entry: CatalogEntry;
  bundle?: SkillBundle;
  onRead: (name: string) => void;
  onRemoved: () => void;
}) {
  const t = useT();
  const name = entry.name ?? "";
  const state = entry.installed ?? "";
  return (
    <div className="space-y-4">
      {entry.description ? <p className="text-sm">{entry.description}</p> : null}
      <div className="space-y-1">
        <Portability entry={entry} />
        <PortabilityReason entry={entry} />
        <Requires entry={entry} />
        {/* Measured from the skill's own text — a fact about what it calls, apart from the verdict,
            which is a judgement about whether it works here. */}
        {entry.missing_tools && entry.missing_tools.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            {t("catalog.mentions")} <span className="font-mono">{entry.missing_tools.join(", ")}</span>
          </p>
        ) : null}
      </div>
      <Facts>
        <Fact label={t("catalogUi.group.cat")}>{entry.topic || "—"}</Fact>
        <Fact label={t("catalog.filter.state")}>
          {state ? t(`catalog.state.${state}`) : t("catalog.filter.notInstalled")}
        </Fact>
        <Fact label={t("skills.detail.licence")}>
          <LicenceChip license={entry.license} permissive={entry.permissive} />
        </Fact>
        <Fact label={t("plugins.detail.source")}>
          <div className="space-y-1">
            <SourceLink href={entry.homepage ?? ""} name={name} />
            <Commit bundle={bundle} />
            {/* Several of these are ports of somebody else's work and say so upstream. */}
            {entry.author ? <p>{t("catalog.by", { author: entry.author })}</p> : null}
          </div>
        </Fact>
      </Facts>
      <Reaches>
        <p className="text-xs text-muted-foreground">{t("skills.detail.reachesBundle")}</p>
        {state ? <ReadText name={name} onRead={onRead} /> : null}
      </Reaches>
      {bundle?.reconfirm ? <p className="text-xs text-warn-foreground">{t("catalog.reconfirm")}</p> : null}
      {state ? (
        <div className="space-y-2">
          <UpdateCheck name={name} />
          <Uninstall name={name} onRemoved={onRemoved} />
        </div>
      ) : null}
    </div>
  );
}

export function BundleBody({
  bundle,
  onRead,
  onRemoved,
}: {
  bundle: SkillBundle;
  onRead: (name: string) => void;
  onRemoved: () => void;
}) {
  const t = useT();
  const name = bundle.name ?? "";
  const upload = bundle.origin === "upload";
  return (
    <div className="space-y-4">
      {bundle.description ? <p className="text-sm">{bundle.description}</p> : null}
      {upload ? <Badge tone="warn">{t("skills.upload.untrusted")}</Badge> : null}
      <Facts>
        <Fact label={t("catalog.filter.state")}>{t(`catalog.state.${bundle.status || "pending"}`)}</Fact>
        <Fact label={t("skills.detail.licence")}>
          <LicenceChip license={bundle.license} permissive={false} />
        </Fact>
        <Fact label={t("plugins.detail.source")}>
          <div className="space-y-1">
            <p className="break-all font-mono">{bundle.source}</p>
            <Commit bundle={bundle} />
          </div>
        </Fact>
      </Facts>
      <Reaches>
        <p className="text-xs text-muted-foreground">{t("skills.detail.reachesBundle")}</p>
        <ReadText name={name} onRead={onRead} />
      </Reaches>
      {bundle.reconfirm ? <p className="text-xs text-warn-foreground">{t("catalog.reconfirm")}</p> : null}
      <div className="space-y-2">
        {upload ? null : <UpdateCheck name={name} />}
        <Uninstall name={name} onRemoved={onRemoved} />
      </div>
    </div>
  );
}

export function LearnedBody({ stat, candidate }: { stat: SkillStat; candidate: boolean }) {
  const t = useT();
  const effective = useQuery({ queryKey: ["skills-effective"], queryFn: getEffectiveSkills });
  const data = effective.data;
  return (
    <div className="space-y-4">
      <p className="text-sm">
        {t("skills.stats", { uses: stat.uses, wins: stat.successes })}
        {stat.rate != null && ` · ${Math.round(stat.rate * 100)}%`}
      </p>
      <LearnedBadges stat={stat} candidate={candidate} />
      <Facts>
        <Fact label={t("plugins.detail.source")}>{t("skills.detail.sourceLearned")}</Fact>
        <Fact label={t("skills.detail.licence")}>{t("skills.detail.noLicense")}</Fact>
      </Facts>
      <Reaches>
        {/* Cards are chosen per task, so the honest answer is the rule and the pool. Off — the
            measured default — says nothing is read, so an active card with zero uses stops
            looking broken. */}
        <p className="text-xs text-muted-foreground">
          {data?.cards_read
            ? t("skills.active.cardsOn", { k: data.cards_k ?? 0, n: (data.cards ?? []).length })
            : t("skills.active.cardsOff")}
        </p>
      </Reaches>
    </div>
  );
}

/** The body is fetched only when a card is actually opened — the list carries metadata alone. */
export function LibraryBody({ card }: { card: LibraryCard }) {
  const t = useT();
  const full = useQuery({ queryKey: ["skill-library", card.name], queryFn: () => getSkillLibraryCard(card.name) });
  const stage = (STAGES as readonly string[]).includes(card.stage)
    ? t(`skills.stage.${card.stage}`)
    : t("skills.stageNone");
  return (
    <div className="space-y-4">
      {card.description ? <p className="text-sm">{card.description}</p> : null}
      {card.triggers.length > 0 ? (
        <p className="text-xs text-muted-foreground">
          {t("skills.cardTriggers")}: {card.triggers.join(" · ")}
        </p>
      ) : null}
      <Facts>
        <Fact label={t("skills.detail.stage")}>{stage}</Fact>
        <Fact label={t("catalogUi.group.cat")}>{card.topic || "—"}</Fact>
        <Fact label={t("skills.detail.licence")}>
          <LicenceChip license={card.license} permissive={isPermissive(card.license)} />
        </Fact>
        <Fact label={t("plugins.detail.source")}>{t("skills.detail.sourceLibrary")}</Fact>
      </Facts>
      <Reaches>
        <p className="text-xs text-muted-foreground">{t("skills.detail.reachesLibrary")}</p>
        {/* The card's own markdown, shown as text rather than rendered: it is what the agent reads
            verbatim, and a renderer would show a prettier document than the one the model gets. */}
        {full.isError ? (
          <ErrorState error={full.error} onRetry={() => void full.refetch()} />
        ) : full.isLoading || !full.data ? (
          <Spinner />
        ) : (
          <pre className={PRE}>{full.data.body}</pre>
        )}
      </Reaches>
    </div>
  );
}

export function PinnedBody({ item, catalog }: { item: PinnedCatalogItem; catalog: PinnedCatalog }) {
  const t = useT();
  const s = item.source;
  const href = s.owner && s.repo && s.sha ? `https://github.com/${s.owner}/${s.repo}/blob/${s.sha}/${s.path}` : "";
  return (
    <div className="space-y-4">
      {item.description ? <p className="text-sm">{item.description}</p> : null}
      <p className="text-xs text-warn-foreground">{item.refusal || t("skills.pinned.readOnly")}</p>
      <Facts>
        <Fact label={t("catalogUi.group.cat")}>{item.category}</Fact>
        <Fact label={t("skills.detail.licence")}>
          <LicenceChip license={item.license} permissive={isPermissive(item.license)} />
        </Fact>
        <Fact label={t("plugins.detail.source")}>
          <div className="space-y-1">
            <p className="break-all font-mono">
              {`${s.owner}/${s.repo}@${s.sha.slice(0, 7)}`} · {s.path}
            </p>
            <p className="text-muted-foreground">
              {t("skills.discover.pinnedSource", { repo: catalog.repo, sha: catalog.sha.slice(0, 7) })}
            </p>
            <SourceLink href={href} name={item.name} />
            {item.author ? <p>{t("catalog.by", { author: item.author })}</p> : null}
          </div>
        </Fact>
      </Facts>
    </div>
  );
}
