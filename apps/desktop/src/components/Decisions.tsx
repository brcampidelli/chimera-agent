import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { getDecisions, labelDecision } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/async";
import { useI18n, useP, useT, type TFunc } from "@/lib/i18n";
import type { Decisions as DecisionsData } from "@/lib/types";

type Group = DecisionsData["groups"][number];
type Row = DecisionsData["recent"][number];

/**
 * The Decisions screen — study 22, phase 4.
 *
 * What the typed decisions answered and whether anyone has said what was true. Three parts, in the
 * order a person needs them: the declared decision points (what may ask, and what it may do — only
 * ever escalate); per instrument, what the log holds (the review budget — cards per hundred decisions,
 * which is what the band costs a person — how many answers carry a label and from which region of the
 * band, and, where labels exist, how the number compares with what happened); and the latest answers,
 * each labelable here, including the ALLOW region that no approval card ever reaches.
 *
 * A number here is never a verdict about the screen's own reader: the reliability rows show predicted
 * against observed only on labelled rows, and say how many there are.
 */
export function Decisions({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const q = useQuery({ queryKey: ["decisions"], queryFn: () => getDecisions() });
  const title = t("decisions.title");
  const icon = <Scale className="h-5 w-5" />;

  if (q.isError) {
    return (
      <Screen title={title} icon={icon} embedded={embedded}>
        <Panel>
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        </Panel>
      </Screen>
    );
  }
  if (q.isLoading || !q.data) {
    return (
      <Screen title={title} icon={icon} embedded={embedded}>
        <Panel>
          <Spinner />
        </Panel>
      </Screen>
    );
  }
  const data = q.data;
  return (
    <Screen title={title} icon={icon} embedded={embedded}>
      <Panel title={t("decisions.points")}>
        {data.specs.length === 0 ? (
          <EmptyState text={t("decisions.noPoints")} />
        ) : (
          data.specs.map((spec) => (
            <div key={spec.name} className="flex flex-wrap items-center gap-2 border-b border-border py-2 last:border-b-0">
              <span className="font-mono text-sm font-semibold">{spec.name}</span>
              <Badge tone="muted">{spec.mode === "enforce" ? t("decisions.mode.enforce") : t("decisions.mode.shadow")}</Badge>
              <Badge tone="warn">{t("decisions.escalates", { to: spec.escalation })}</Badge>
              <span className="text-xs text-muted-foreground">{spec.description}</span>
              <span className="w-full font-mono text-xs text-muted-foreground">{spec.bench}</span>
            </div>
          ))
        )}
      </Panel>
      {data.groups.length === 0 ? (
        <Panel>
          <EmptyState text={t("decisions.empty")} />
        </Panel>
      ) : (
        data.groups.map((g) => <GroupPanel key={`${g.decision}/${g.prompt_hash}/${g.resolved_model}`} group={g} data={data} t={t} />)
      )}
      <RecentPanel rows={data.recent} t={t} />
    </Screen>
  );
}

function GroupPanel({ group: g, data, t }: { group: Group; data: DecisionsData; t: TFunc }) {
  const pct = useP();
  const { lang } = useI18n();
  // One decimal, in the chosen language — `toFixed` would print `25.0` inside a Portuguese sentence.
  const oneDecimal = new Intl.NumberFormat(lang, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const r = g.regions;
  const by = g.labelled_by_region;
  const onlyReview = g.labelled > 0 && (by.review ?? 0) === g.labelled;
  return (
    <Panel title={`${g.decision} · ${g.model}${g.resolved_model ? ` (${g.resolved_model})` : ""}`}>
      <div className="space-y-1 text-sm">
        <p>{t("decisions.counts", { answers: g.answers, halts: g.halts, cached: g.cached, calibrated: g.calibrated })}</p>
        <p>
          {t("decisions.regions", {
            review: r.review ?? 0, uncertain: r.uncertain ?? 0, allow: r.allow ?? 0, none: r.no_p ?? 0,
            reviewAt: pct(data.review_at), allowBelow: pct(data.allow_below),
          })}
        </p>
        <p className="font-medium">
          {g.review_per_100 === null || g.review_per_100 === undefined
            ? t("decisions.budget.none")
            : t("decisions.budget", { n: oneDecimal.format(g.review_per_100) })}
        </p>
        <p>{t("decisions.labelled", { n: g.labelled, positives: g.positives, review: by.review ?? 0, uncertain: by.uncertain ?? 0, allow: by.allow ?? 0 })}</p>
        {onlyReview ? <p className="text-xs text-warn-foreground">{t("decisions.onlyReview")}</p> : null}
        {g.catch || g.false_refusal ? (
          <p>
            {t("decisions.operating", {
              caught: g.catch ? `${g.catch[0]}/${g.catch[1]}` : "—",
              refused: g.false_refusal ? `${g.false_refusal[0]}/${g.false_refusal[1]}` : "—",
            })}
          </p>
        ) : null}
      </div>
      <Reliability bins={g.reliability} t={t} />
    </Panel>
  );
}

/** Predicted against observed, one row per band of p — drawn as two bars so the gap is the reading. */
function Reliability({ bins, t }: { bins: Group["reliability"]; t: TFunc }) {
  const pct = useP();
  const labelled = bins.reduce((sum, b) => sum + b.n, 0);
  if (labelled === 0) {
    return <p className="mt-2 text-xs text-muted-foreground">{t("decisions.reliability.none")}</p>;
  }
  return (
    <div className="mt-3" role="table" aria-label={t("decisions.reliability.title")}>
      <p className="mb-1 text-xs font-medium">{t("decisions.reliability.title")}</p>
      {bins.map((b) => (
        <div key={b.lo} role="row" className="flex items-center gap-2 text-xs">
          <span role="cell" className="w-20 font-mono tabular-nums">{`${pct(b.lo)}–${pct(b.hi)}`}</span>
          <span role="cell" className="w-10 tabular-nums text-muted-foreground">n={b.n}</span>
          <span role="cell" className="flex-1">
            {b.n > 0 && b.mean_p !== null && b.mean_p !== undefined && b.observed !== null && b.observed !== undefined ? (
              <span className="flex flex-col gap-0.5">
                <span className="block h-1.5 rounded bg-accent/60" style={{ width: `${Math.round(b.mean_p * 100)}%` }} title={t("decisions.reliability.predicted")} />
                <span className="block h-1.5 rounded bg-foreground/60" style={{ width: `${Math.round(b.observed * 100)}%` }} title={t("decisions.reliability.observed")} />
              </span>
            ) : null}
          </span>
          <span role="cell" className="w-28 text-right tabular-nums text-muted-foreground">
            {b.n > 0 && b.mean_p != null && b.observed != null ? `${pct(b.mean_p)} → ${pct(b.observed)}` : ""}
          </span>
        </div>
      ))}
      <p className="mt-1 text-xs text-muted-foreground">{t("decisions.reliability.legend")}</p>
    </div>
  );
}

function RecentPanel({ rows, t }: { rows: Row[]; t: TFunc }) {
  const pct = useP();
  const { lang } = useI18n();
  const qc = useQueryClient();
  const label = useMutation({
    mutationFn: ({ id, event }: { id: string; event: boolean }) => labelDecision(id, event),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["decisions"] }),
  });
  return (
    <Panel title={t("decisions.recent")}>
      {rows.length === 0 ? (
        <EmptyState text={t("decisions.recent.empty")} />
      ) : (
        rows.map((row) => (
          <div key={row.id} className="flex flex-wrap items-center gap-2 border-b border-border py-2 text-xs last:border-b-0">
            <span className="font-mono text-muted-foreground">{new Date(row.at * 1000).toLocaleString(lang)}</span>
            <span className="font-mono">{row.decision}</span>
            <span className="font-mono tabular-nums">
              {row.halt ? t("decisions.halt") : row.p === null || row.p === undefined ? "—" : `p=${pct(row.p)}`}
            </span>
            {row.p != null && !row.calibrated ? <Badge tone="muted">{t("decisions.uncalibrated")}</Badge> : null}
            {row.cached ? <Badge tone="muted">{t("decisions.cached")}</Badge> : null}
            <span className="w-full truncate font-mono text-muted-foreground" title={row.state}>{row.state}</span>
            {row.label === null || row.label === undefined ? (
              <span className="flex items-center gap-1">
                <span className="text-muted-foreground">{t("decisions.askLabel")}</span>
                <Button size="sm" variant="ghost" disabled={label.isPending} onClick={() => label.mutate({ id: row.id, event: true })}>
                  {t("decisions.label.yes")}
                </Button>
                <Button size="sm" variant="ghost" disabled={label.isPending} onClick={() => label.mutate({ id: row.id, event: false })}>
                  {t("decisions.label.no")}
                </Button>
              </span>
            ) : (
              <Badge tone={row.label === 1 ? "warn" : "muted"}>
                {row.label === 1 ? t("decisions.labelled.yes") : t("decisions.labelled.no")}
              </Badge>
            )}
          </div>
        ))
      )}
    </Panel>
  );
}
