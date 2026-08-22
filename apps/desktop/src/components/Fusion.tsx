import type {
  CascadeMeta,
  FusionMeta,
  FusionPanelEntry,
  FusionStage,
  RouteMeta,
} from "@/lib/types";
import { Badge, EmptyState, Panel } from "@/components/ui/panel";
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

/** The engine's stage identifier -> the key its human name lives under.
 *
 *  `synth` and `synthesizer` are the same role with two spellings: the Literal the engine routes on,
 *  and the word a person reads. Mapping rather than renaming either, because the Literal is a wire
 *  value and the name is a translation. */
const STAGE_KEY: Record<string, string> = { panel: "panel", judge: "judge", synth: "synthesizer" };

function PanelRow({ entry }: { entry: FusionPanelEntry }) {
  const t = useT();
  const tok = tokens(entry.prompt_tokens, entry.completion_tokens);
  return (
    <div className="px-4 py-3">
      <div className="flex items-center gap-1.5">
        {/* This panel is 288px wide (`Activity` is `w-72`), and a slug like
            `openrouter/deepseek/deepseek-chat-v3.1` does not fit. Truncated with the full value in
            the title, rather than left to overflow the row and push the badges out of it. */}
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground" title={entry.model}>
          {entry.model}
        </span>
        {entry.error ? <Badge tone="bad">{t("fusion.error")}</Badge> : null}
        {tok ? <span className="shrink-0 text-xs text-muted-foreground">{tok}</span> : null}
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
  const t = useT();
  const tok = tokens(stage.prompt_tokens, stage.completion_tokens);
  return (
    <div className="flex items-center gap-2 px-4 py-3">
      {/* The role's NAME, not its identifier. `stage.stage` is "panel" / "judge" / "synth" — a
          Literal the engine uses to route, and three untranslated English words on a screen that
          has proper names for all three, in ten languages. */}
      <Badge tone="muted">{t(`fusion.role.${STAGE_KEY[stage.stage] ?? stage.stage}`)}</Badge>
      {/* Truncated with the full slug in the title, same reason as the panel row above. */}
      <span
        className="min-w-0 flex-1 truncate font-mono text-xs text-foreground"
        title={stage.model}
      >
        {stage.model}
      </span>
      {tok ? <span className="shrink-0 text-xs text-muted-foreground">{tok}</span> : null}
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
            {/* How the panel's answers became one: synthesized, or decided by majority. Another
                wire value that was being shown as a word — `aggregation` is a Literal the engine
                routes on, and "synth" is not a thing a reader is supposed to know. */}
            <Badge tone="muted">{t(`fusion.aggregation.${meta.aggregation}`)}</Badge>
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
          <EmptyState text={t("fusion.panelEmpty")} />
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
                <span className="shrink-0 text-xs text-muted-foreground">
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

/**
 * How the last turn was composed.
 *
 * No longer a rail destination. It was structurally a *turn detail* pretending to be a place: it
 * rendered only when the immediately preceding chat turn had used fusion, so seeing it meant
 * sending a fused message, navigating away, and reading it before the next message erased it. As a
 * section of the activity inspector it sits beside the turn it describes, and is more discoverable
 * for losing its icon, not less.
 */
export function Fusion({ report }: { report?: { route_meta?: RouteMeta | null } | null }) {
  const t = useT();
  const meta = report?.route_meta;
  // Nothing to show unless this turn actually routed through fusion or the cascade. Rendering an
  // empty panel here would put a permanent "no data" box in the inspector.
  if (!meta) return null;
  return (
    <div className="space-y-3">
      {meta.kind === "fusion" ? (
        <FusionBreakdown meta={meta} t={t} />
      ) : (
        <CascadeRoute meta={meta} t={t} />
      )}
    </div>
  );
}
