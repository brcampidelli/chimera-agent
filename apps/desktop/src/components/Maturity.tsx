import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { getBenchmarks, getMaturity } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { useT, type TFunc } from "@/lib/i18n";
import type {
  BenchmarkExternal,
  BenchmarkLift,
  Benchmarks as BenchmarksData,
  Maturity as MaturityData,
  MaturitySurface,
} from "@/lib/types";

/** Render a 0..1 ratio as a whole percent. */
const pct = (n: number): string => `${Math.round(n * 100)}%`;

/** A 0..1 ratio as a whole-percent delta with an explicit sign (e.g. +50pp, -5pp). */
const signedPP = (n: number): string => `${n >= 0 ? "+" : ""}${Math.round(n * 100)}pp`;

/** A [lo, hi] CI (fractions) as a one-decimal percent range — never rounded to hide that it spans 0. */
const ciText = (ci: number[]): string =>
  `[${(ci[0] * 100).toFixed(1)}%, ${(ci[1] * 100).toFixed(1)}%]`;

/** The honest caveat line that MUST sit next to every lift number: n · CI · significance. */
function Caveat({ n, ci, significant, t }: { n: number; ci: number[]; significant: boolean; t: TFunc }) {
  return (
    <span className="text-[11px] text-muted-foreground">
      {t("maturity.bench.n")}={n} · {t("maturity.bench.ci")} {ciText(ci)} ·{" "}
      {significant ? (
        <span className="text-ok">{t("maturity.bench.significant")}</span>
      ) : (
        <span className="font-semibold text-[hsl(38_92%_62%)]">
          {t("maturity.bench.notSignificant")}
        </span>
      )}
    </span>
  );
}

/** The promising weak-model lift: a cheap model + Chimera vs the cheap model alone. Shown honestly —
 *  the big +Δpp sits directly above its "n / CI / not significant" caveat, never in isolation. */
function WeakLiftCard({ lift, t }: { lift: BenchmarkLift; t: TFunc }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-semibold text-foreground">
          {t("maturity.bench.weakLift")}
        </span>
        <Badge tone="accent">{signedPP(lift.delta)}</Badge>
      </div>
      <p className="mt-1 font-mono text-[11px] text-muted-foreground">{lift.model}</p>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="font-mono text-sm text-muted-foreground">{pct(lift.baseline_rate)}</span>
        <span className="text-muted-foreground">→</span>
        <span className="font-mono text-lg text-foreground">{pct(lift.treatment_rate)}</span>
      </div>
      <div className="mt-1.5">
        <Caveat n={lift.n} ci={lift.ci} significant={lift.significant} t={t} />
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        {t("maturity.bench.suite")}: {lift.suite}
      </p>
    </div>
  );
}

/** One recorded external benchmark (e.g. Terminal-Bench): the humbling number, published with its
 *  caveat and the honest note that the scaffold didn't lift an already-competent model. */
function ExternalRow({ row, t }: { row: BenchmarkExternal; t: TFunc }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-semibold text-foreground">{row.benchmark}</span>
        <Badge tone={row.delta >= 0 ? "accent" : "muted"}>{signedPP(row.delta)}</Badge>
      </div>
      <p className="mt-1 font-mono text-[11px] text-muted-foreground">{row.model}</p>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="font-mono text-sm text-muted-foreground">{pct(row.baseline_rate)}</span>
        <span className="text-muted-foreground">→</span>
        <span className="font-mono text-sm text-foreground">{pct(row.treatment_rate)}</span>
      </div>
      <div className="mt-1.5">
        <Caveat n={row.n} ci={row.ci} significant={row.significant} t={t} />
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">{row.note}</p>
    </div>
  );
}

/** The Benchmarks section: the app's REAL recorded performance, promising-internal + humbling-external
 *  side by side, each number pinned to its significance caveat. Honest empty-state when unavailable. */
function Benchmarks({ t }: { t: TFunc }) {
  const q = useQuery({ queryKey: ["benchmarks"], queryFn: getBenchmarks });
  const data: BenchmarksData | undefined = q.data;

  return (
    <Panel title={t("maturity.bench.title")}>
      {q.isLoading ? (
        <Spinner />
      ) : !data || !data.available ? (
        <EmptyState text={t("maturity.bench.empty")} />
      ) : (
        <>
          {data.internal_lift && <WeakLiftCard lift={data.internal_lift} t={t} />}
          {data.external.length > 0 && (
            <p className="bg-white/[0.02] px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t("maturity.bench.external")}
            </p>
          )}
          {data.external.map((row) => (
            <ExternalRow key={row.benchmark} row={row} t={t} />
          ))}
          <p className="px-4 py-3 text-[11px] text-muted-foreground">{t("maturity.bench.humbleNote")}</p>
        </>
      )}
    </Panel>
  );
}

/** Maturity band → tone: GA is good, Beta is accent, Alpha (and anything unknown) is a warning. */
type Tone = "ok" | "accent" | "warn";
const levelTone = (level: string): Tone =>
  level === "GA" ? "ok" : level === "Beta" ? "accent" : "warn";

/** A compact stat tile (label + value), mirrors Usage.tsx / Governance.tsx. */
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

/** One surface row: name (+ optional "weakest" flag), level badge, proven/total (ratio%), and the
 *  missing coverage-IDs as muted chips when the surface is incomplete. */
function SurfaceRow({
  row,
  weakest,
  t,
}: {
  row: MaturitySurface;
  weakest: boolean;
  t: TFunc;
}) {
  return (
    <div className={weakest ? "px-4 py-3 ring-1 ring-inset ring-warn/25" : "px-4 py-3"}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate font-mono text-xs text-foreground">{row.name}</span>
          {weakest && <Badge tone="warn">{t("maturity.weakest")}</Badge>}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <Badge tone={levelTone(row.level)}>{row.level}</Badge>
          <span className="font-mono text-[11px] text-muted-foreground">
            {row.proven}/{row.total} ({pct(row.ratio)})
          </span>
        </span>
      </div>
      {row.missing.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">{t("maturity.missing")}</span>
          {row.missing.map((id) => (
            <Badge key={id} tone="muted">
              {id}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}

export function Maturity() {
  const t = useT();
  const q = useQuery({ queryKey: ["maturity"], queryFn: getMaturity });

  if (q.isError) {
    return (
      <Screen title={t("maturity.title")} icon={<Gauge className="h-5 w-5" />}>
        <Panel>
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        </Panel>
      </Screen>
    );
  }
  if (q.isLoading) {
    return (
      <Screen title={t("maturity.title")} icon={<Gauge className="h-5 w-5" />}>
        <Panel>
          <Spinner />
        </Panel>
      </Screen>
    );
  }

  const data: MaturityData | undefined = q.data;

  if (!data || !data.available) {
    return (
      <Screen title={t("maturity.title")} icon={<Gauge className="h-5 w-5" />}>
        <Panel>
          <EmptyState text={t("maturity.unavailable")} />
        </Panel>
      </Screen>
    );
  }

  return (
    <Screen title={t("maturity.title")} icon={<Gauge className="h-5 w-5" />}>
      <Panel title={t("maturity.overall")}>
        <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-3">
          <Tile label={t("maturity.proven")} value={`${data.proven}/${data.total}`} />
          <div className="surface flex flex-col gap-1 p-4">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t("maturity.level")}
            </span>
            <span>
              <Badge tone={levelTone(data.level)}>{data.level}</Badge>
            </span>
          </div>
          <Tile label={t("maturity.coverage")} value={pct(data.ratio)} />
        </div>
        {data.source === "snapshot" && data.generated_for && (
          <p className="px-4 py-3 text-[11px] text-muted-foreground">
            {t("maturity.snapshotNote", { version: data.generated_for })}
          </p>
        )}
      </Panel>

      <Panel title={t("maturity.bySurface")}>
        {data.surfaces.map((s) => (
          <SurfaceRow key={s.name} row={s} weakest={data.weakest?.name === s.name} t={t} />
        ))}
      </Panel>

      <p className="px-1 text-[11px] text-muted-foreground">{t("maturity.coverageNote")}</p>

      <Benchmarks t={t} />
    </Screen>
  );
}
