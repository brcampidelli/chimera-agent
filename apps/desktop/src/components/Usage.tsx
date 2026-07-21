import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { getUsage } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { useT, type TFunc } from "@/lib/i18n";
import type { UsageSummary } from "@/lib/types";

/** Grouped rows carry a leading key field (day / model / session_id); type them structurally. */
type DayRow = UsageSummary["by_day"][number];
type ModelRow = UsageSummary["by_model"][number];
type SessionRow = UsageSummary["by_session"][number];

const num = (n: number): string => n.toLocaleString();
const usd = (n: number): string => `$${n.toFixed(4)}`;
/** Honest per-group price: "—" when EVERY turn in the group is unpriced (unknown cost — never a fake
 *  $0.0000); otherwise the summed cost of the priced turns. */
const groupUsd = (v: number, unpriced: number, turns: number): string =>
  unpriced >= turns ? "—" : usd(v);

/** A compact stat tile (label + value), reused for the totals row and the small stats. */
function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="surface flex flex-col gap-1 p-4">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className="font-mono text-xl text-foreground">{value}</span>
      {note && <span className="text-[11px] text-muted-foreground">{note}</span>}
    </div>
  );
}

/** Hand-rolled SVG bar chart of a value-per-day series (no chart dependency). Bars use the accent
 *  gradient via CSS vars, so they follow the theme; each bar has an accessible <title>. */
function DayBars({
  days,
  chartUsd,
  t,
}: {
  days: DayRow[];
  chartUsd: boolean;
  t: TFunc;
}) {
  const value = (d: DayRow): number => (chartUsd ? d.usd : d.prompt_tokens + d.completion_tokens);
  const label = (d: DayRow): string =>
    chartUsd ? groupUsd(d.usd, d.unpriced, d.turns) : num(d.prompt_tokens + d.completion_tokens);
  const max = Math.max(...days.map(value), 1);
  const slot = 44;
  const barW = 26;
  const chartH = 120;
  const labelH = 22;
  const W = Math.max(days.length * slot, slot);
  const H = chartH + labelH;

  return (
    <div className="overflow-x-auto px-4 py-4">
      <div className="mb-2 text-[11px] text-muted-foreground">
        {chartUsd ? t("usage.spend") : t("usage.tokens")}
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width={W}
        height={H}
        role="img"
        aria-label={chartUsd ? t("usage.spend") : t("usage.tokens")}
        className="max-w-full"
      >
        <defs>
          <linearGradient id="usageBar" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(var(--accent))" />
            <stop offset="100%" stopColor="hsl(var(--accent2))" />
          </linearGradient>
        </defs>
        {days.map((d, i) => {
          const v = value(d);
          const h = Math.max((chartH * v) / max, v > 0 ? 2 : 0);
          const x = i * slot + (slot - barW) / 2;
          const y = chartH - h;
          return (
            <g key={d.day}>
              <title>
                {d.day} · {label(d)} · {t("usage.turns")}: {num(d.turns)}
              </title>
              <rect
                x={x}
                y={y}
                width={barW}
                height={h}
                rx={3}
                fill="url(#usageBar)"
              />
              <text
                x={i * slot + slot / 2}
                y={H - 7}
                textAnchor="middle"
                className="fill-muted-foreground"
                fontSize="9"
              >
                {d.day.slice(5)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** A ranked horizontal bar (CSS width, accent gradient) with the model's turns/tokens/usd. */
function ModelBar({ row, max }: { row: ModelRow; max: number }) {
  const tokens = row.prompt_tokens + row.completion_tokens;
  const pct = Math.max((row.turns / max) * 100, 3);
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-xs text-foreground">{row.model || "—"}</span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
          {num(row.turns)} · {num(tokens)} · {groupUsd(row.usd, row.unpriced, row.turns)}
        </span>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-chip bg-white/[0.05]">
        <div className="h-full rounded-chip bg-accent-grad" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Usage() {
  const t = useT();
  const q = useQuery({ queryKey: ["usage"], queryFn: getUsage });

  if (q.isError) {
    return (
      <Screen title={t("usage.title")} icon={<BarChart3 className="h-5 w-5" />}>
        <Panel>
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        </Panel>
      </Screen>
    );
  }
  if (q.isLoading) {
    return (
      <Screen title={t("usage.title")} icon={<BarChart3 className="h-5 w-5" />}>
        <Panel>
          <Spinner />
        </Panel>
      </Screen>
    );
  }

  const data = q.data;
  const totals = data?.totals;

  if (!data || !totals || totals.turns === 0) {
    return (
      <Screen title={t("usage.title")} icon={<BarChart3 className="h-5 w-5" />}>
        <Panel>
          <EmptyState text={t("usage.empty")} />
        </Panel>
      </Screen>
    );
  }

  const totalTokens = totals.prompt_tokens + totals.completion_tokens;
  // Chart spend/day only when we actually know some prices; otherwise chart tokens/day and say so.
  const chartUsd = totals.usd > 0;
  const cacheHit =
    data.cache_hit_pct == null ? "—" : `${Math.round(data.cache_hit_pct * 100)}%`;
  const mix = data.route_mix;
  const models: ModelRow[] = data.by_model;
  const maxModelTurns = Math.max(...models.map((m) => m.turns), 1);
  const sessions: SessionRow[] = data.by_session;

  return (
    <Screen title={t("usage.title")} icon={<BarChart3 className="h-5 w-5" />}>
      <Panel title={t("usage.totals")}>
        <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-3">
          <Tile label={t("usage.turns")} value={num(totals.turns)} />
          <Tile label={t("usage.tokens")} value={num(totalTokens)} />
          <Tile
            label={t("usage.spend")}
            value={usd(totals.usd)}
            note={totals.unpriced_turns > 0 ? t("usage.unpriced", { n: totals.unpriced_turns }) : undefined}
          />
        </div>
      </Panel>

      <Panel title={t("usage.byDay")}>
        <DayBars days={data.by_day} chartUsd={chartUsd} t={t} />
      </Panel>

      <Panel title={t("usage.byModel")}>
        {models.map((m) => (
          <ModelBar key={m.model || "unknown"} row={m} max={maxModelTurns} />
        ))}
      </Panel>

      <Panel title={t("usage.bySession")}>
        {sessions.map((s) => {
          const tokens = s.prompt_tokens + s.completion_tokens;
          return (
            <div key={s.session_id} className="flex items-center justify-between gap-2 px-4 py-3">
              <span className="truncate font-mono text-xs text-foreground">{s.session_id}</span>
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {num(s.turns)} · {num(tokens)} · {groupUsd(s.usd, s.unpriced, s.turns)}
              </span>
            </div>
          );
        })}
      </Panel>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label={t("usage.cacheHit")} value={cacheHit} />
        <div className="surface flex flex-col gap-2 p-4">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("usage.routeMix")}
          </span>
          <div className="flex flex-wrap gap-1.5">
            <Badge tone="muted">
              {t("usage.single")} {num(mix.single ?? 0)}
            </Badge>
            <Badge tone="accent">
              {t("usage.fusion")} {num(mix.fusion ?? 0)}
            </Badge>
            <Badge tone="ok">
              {t("usage.cascade")} {num(mix.cascade ?? 0)}
            </Badge>
          </div>
        </div>
      </div>
    </Screen>
  );
}
