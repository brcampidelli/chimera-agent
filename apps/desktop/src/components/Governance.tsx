import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { getGovernanceAudit, getGovernanceInjection } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { useT, type TFunc } from "@/lib/i18n";
import type { GovernanceAudit, InjectionReport } from "@/lib/types";

type CategoryRow = InjectionReport["by_category"][number];
type AttackRow = InjectionReport["attacks"][number];
type AuditEvent = GovernanceAudit["events"][number];

/** Honest percentage: ASR/block-rate are 0..1 fractions; render as a whole percent (lower = better). */
const pct = (n: number): string => `${(n * 100).toFixed(0)}%`;

/** A compact stat tile (label + value), mirrors Usage.tsx's Tile. */
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

/** One category row: name + two mini bars comparing undefended (bad) vs defended (ok) ASR. Bar width
 *  is the ASR fraction, so a shorter defended bar reads as "fewer attacks got through". */
function CategoryRowView({ row, t }: { row: CategoryRow; t: TFunc }) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-xs text-foreground">{row.category}</span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
          {t("governance.injection.undefended")} {pct(row.undefended_asr)} →{" "}
          {t("governance.injection.defended")} {pct(row.defended_asr)}
        </span>
      </div>
      <div className="mt-1.5 space-y-1">
        <div className="h-2 overflow-hidden rounded-chip bg-white/[0.05]">
          <div
            className="h-full rounded-chip bg-bad/70"
            style={{ width: `${Math.max(row.undefended_asr * 100, row.undefended_asr > 0 ? 3 : 0)}%` }}
          />
        </div>
        <div className="h-2 overflow-hidden rounded-chip bg-white/[0.05]">
          <div
            className="h-full rounded-chip bg-ok/70"
            style={{ width: `${Math.max(row.defended_asr * 100, row.defended_asr > 0 ? 3 : 0)}%` }}
          />
        </div>
      </div>
    </div>
  );
}

/** A blocked ✓ / not-blocked ✗ glyph, tone-coded. */
function BlockedGlyph({ blocked }: { blocked: boolean }) {
  return (
    <span
      className={blocked ? "text-ok" : "text-bad"}
      title={blocked ? "blocked" : "got through"}
    >
      {blocked ? "✓" : "✗"}
    </span>
  );
}

/** Per-attack table: id, category badge, harmful tool (mono), defended/undefended blocked glyphs. */
function AttackRowView({ row, t }: { row: AttackRow; t: TFunc }) {
  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">{row.id}</span>
      <Badge tone="muted">{row.category}</Badge>
      <span className="hidden shrink-0 font-mono text-[11px] text-muted-foreground sm:inline">
        {row.harmful_tool}
      </span>
      <span className="flex shrink-0 items-center gap-2 font-mono text-xs">
        <span className="flex items-center gap-1">
          <span className="text-[10px] uppercase text-muted-foreground">
            {t("governance.injection.defended")}
          </span>
          <BlockedGlyph blocked={row.blocked_defended} />
        </span>
        <span className="flex items-center gap-1">
          <span className="text-[10px] uppercase text-muted-foreground">
            {t("governance.injection.undefended")}
          </span>
          <BlockedGlyph blocked={row.blocked_undefended} />
        </span>
      </span>
    </div>
  );
}

function InjectionPanel({ data, t }: { data: InjectionReport; t: TFunc }) {
  return (
    <Panel title={t("governance.injection.title")}>
      <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-3">
        <Tile
          label={t("governance.injection.undefendedAsr")}
          value={pct(data.undefended_asr)}
          note={t("governance.injection.lowerBetter")}
        />
        <Tile
          label={t("governance.injection.defendedAsr")}
          value={pct(data.defended_asr)}
          note={t("governance.injection.lowerBetter")}
        />
        <Tile
          label={t("governance.injection.blockRate")}
          value={pct(data.defended_block_rate)}
          note={t("governance.injection.attacks", { n: data.total_attacks })}
        />
      </div>

      <div className="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {t("governance.injection.byCategory")}
      </div>
      {data.by_category.map((row) => (
        <CategoryRowView key={row.category} row={row} t={t} />
      ))}

      <div className="px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {t("governance.injection.attacksTable")}
      </div>
      {data.attacks.map((row) => (
        <AttackRowView key={row.id} row={row} t={t} />
      ))}

      {data.leaks_defended.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
          <span className="text-[11px] text-muted-foreground">
            {t("governance.injection.leaksNote")}
          </span>
          {data.leaks_defended.map((id) => (
            <Badge key={id} tone="muted">
              {id}
            </Badge>
          ))}
        </div>
      ) : null}

      <p className="px-4 py-3 text-[11px] text-muted-foreground">{t("governance.injection.note")}</p>
    </Panel>
  );
}

function AuditPanel({ data, t }: { data: GovernanceAudit; t: TFunc }) {
  if (!data.populated) {
    return (
      <Panel title={t("governance.audit.title")}>
        <EmptyState text={t("governance.audit.empty")} />
      </Panel>
    );
  }
  return (
    <Panel title={t("governance.audit.title")}>
      {data.events.map((e: AuditEvent) => (
        <div key={e.seq} className="flex items-center gap-3 px-4 py-2.5">
          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
            #{e.seq}
          </span>
          <Badge tone="accent">{e.type || "—"}</Badge>
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground">
            {e.summary}
          </span>
        </div>
      ))}
    </Panel>
  );
}

export function Governance() {
  const t = useT();
  const injection = useQuery({ queryKey: ["governance-injection"], queryFn: getGovernanceInjection });
  const audit = useQuery({ queryKey: ["governance-audit"], queryFn: getGovernanceAudit });

  return (
    <Screen title={t("governance.title")} icon={<ShieldCheck className="h-5 w-5" />}>
      {injection.isLoading || !injection.data ? (
        <Panel title={t("governance.injection.title")}>
          <Spinner />
        </Panel>
      ) : (
        <InjectionPanel data={injection.data} t={t} />
      )}

      {audit.isLoading || !audit.data ? (
        <Panel title={t("governance.audit.title")}>
          <Spinner />
        </Panel>
      ) : (
        <AuditPanel data={audit.data} t={t} />
      )}
    </Screen>
  );
}
