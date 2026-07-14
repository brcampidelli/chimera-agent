import { Network } from "lucide-react";
import type {
  CascadeMeta,
  FusionMeta,
  FusionPanelEntry,
  FusionStage,
  TurnReport,
} from "@/lib/types";
import { Badge, EmptyState, Panel, Screen } from "@/components/ui/panel";
import { cn } from "@/lib/utils";
import { useT, type TFunc } from "@/lib/i18n";

const MAX_ANSWER = 600;

function truncate(text: string, n = MAX_ANSWER): string {
  return text.length > n ? `${text.slice(0, n)}…` : text;
}

/** "12 in · 34 out" — omitting either side the provider didn't report; "" when neither is known. */
function tokens(pin: number | null, pout: number | null): string {
  const parts: string[] = [];
  if (pin != null) parts.push(`${pin} in`);
  if (pout != null) parts.push(`${pout} out`);
  return parts.join(" · ");
}

function pct(value: number | null): string | null {
  return value == null ? null : `${Math.round(value * 100)}%`;
}

function PanelRow({ entry }: { entry: FusionPanelEntry }) {
  const tok = tokens(entry.prompt_tokens, entry.completion_tokens);
  return (
    <div className="px-4 py-3">
      <div className="flex items-center gap-1.5">
        <span className="font-mono text-xs text-foreground">{entry.model}</span>
        {entry.error ? <Badge tone="bad">error</Badge> : null}
        {tok ? <span className="text-[11px] text-muted-foreground">{tok}</span> : null}
      </div>
      {entry.error ? (
        <div className="mt-1 whitespace-pre-wrap text-xs text-bad">{entry.error}</div>
      ) : (
        <div className="mt-1 whitespace-pre-wrap text-sm text-muted-foreground">
          {truncate(entry.content)}
        </div>
      )}
    </div>
  );
}

function StageRow({ stage }: { stage: FusionStage }) {
  const tok = tokens(stage.prompt_tokens, stage.completion_tokens);
  return (
    <div className="flex items-center gap-2 px-4 py-3">
      <Badge tone="muted">{stage.stage}</Badge>
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">{stage.model}</span>
      {tok ? <span className="shrink-0 text-[11px] text-muted-foreground">{tok}</span> : null}
    </div>
  );
}

/** The panel -> judge -> synthesis breakdown, reused by the cascade view when it escalated to fusion. */
function FusionBreakdown({ meta, t }: { meta: FusionMeta; t: TFunc }) {
  const diversity = pct(meta.diversity);
  return (
    <>
      <Panel
        title={t("fusion.panel")}
        action={
          <div className="flex items-center gap-1.5">
            <Badge tone="muted">{meta.aggregation}</Badge>
            {meta.early_stopped ? <Badge tone="accent">{t("fusion.earlyStopped")}</Badge> : null}
            {diversity ? (
              <Badge tone="muted">
                {t("fusion.diversity")} {diversity}
              </Badge>
            ) : null}
          </div>
        }
      >
        {meta.panel.length === 0 ? (
          <EmptyState text={t("fusion.empty")} />
        ) : (
          meta.panel.map((entry, i) => <PanelRow key={`${entry.model}-${i}`} entry={entry} />)
        )}
      </Panel>

      {meta.judge_analysis ? (
        <Panel title={t("fusion.judge")}>
          <div className="whitespace-pre-wrap px-4 py-3 font-mono text-xs text-muted-foreground">
            {meta.judge_analysis}
          </div>
        </Panel>
      ) : null}

      {meta.stages.length > 0 ? (
        <Panel title={t("fusion.synthesis")}>
          {meta.stages.map((stage, i) => (
            <StageRow key={`${stage.stage}-${stage.model}-${i}`} stage={stage} />
          ))}
        </Panel>
      ) : null}
    </>
  );
}

function CascadeRoute({ meta, t }: { meta: CascadeMeta; t: TFunc }) {
  const agreement = pct(meta.agreement);
  return (
    <>
      <Panel
        title={t("fusion.route")}
        action={
          <div className="flex items-center gap-1.5">
            {agreement ? (
              <Badge tone="muted">
                {t("fusion.agreement")} {agreement}
              </Badge>
            ) : null}
            <Badge tone="muted">{meta.fuse_reason}</Badge>
          </div>
        }
      >
        {meta.tiers_tried.map((tier) => {
          const accepted = tier === meta.accepted_tier;
          return (
            <div key={tier} className="flex items-center gap-2 px-4 py-3">
              <Badge tone={accepted ? "accent" : "muted"}>{tier}</Badge>
              <span
                className={cn(
                  "min-w-0 flex-1 truncate font-mono text-xs",
                  accepted ? "text-accent" : "text-foreground",
                )}
              >
                {meta.models[tier] ?? "—"}
              </span>
              {tier in meta.tokens_by_tier ? (
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  {meta.tokens_by_tier[tier]} tokens
                </span>
              ) : null}
              {accepted ? <Badge tone="accent">{t("fusion.accepted")}</Badge> : null}
            </div>
          );
        })}
      </Panel>

      {meta.fusion ? <FusionBreakdown meta={meta.fusion} t={t} /> : null}
    </>
  );
}

export function Fusion({ report }: { report?: TurnReport | null }) {
  const t = useT();
  const meta = report?.route_meta;
  return (
    <Screen title={t("fusion.title")} icon={<Network className="h-5 w-5" />}>
      {!meta ? (
        <Panel>
          <EmptyState text={t("fusion.empty")} />
        </Panel>
      ) : meta.kind === "fusion" ? (
        <FusionBreakdown meta={meta} t={t} />
      ) : (
        <CascadeRoute meta={meta} t={t} />
      )}
    </Screen>
  );
}
