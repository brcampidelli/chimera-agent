import { useQuery } from "@tanstack/react-query";
import { Gauge } from "lucide-react";
import { getMaturity } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { useT, type TFunc } from "@/lib/i18n";
import type { Maturity as MaturityData, MaturitySurface } from "@/lib/types";

/** Render a 0..1 ratio as a whole percent. */
const pct = (n: number): string => `${Math.round(n * 100)}%`;

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
    </Screen>
  );
}
