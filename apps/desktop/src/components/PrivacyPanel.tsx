import type { ReactNode } from "react";
import { Badge, Panel } from "@/components/ui/panel";
import type { TFunc } from "@/lib/i18n";
import type { AppConfig } from "@/lib/types";

type Privacy = NonNullable<AppConfig["privacy"]>;

/** One labelled group of the card: a small heading and whatever it holds. */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 px-4 py-3">
      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </span>
      {children}
    </div>
  );
}

function Line({ children }: { children: ReactNode }) {
  return <span className="text-xs text-muted-foreground">{children}</span>;
}

/** Where a prompt goes, and what may be kept — read-only, from this install's configuration.
 *
 *  The answer used to be spread over a dozen model rows on Settings, the OpenRouter route's data
 *  policy had no setting at all, and one surface reached OpenRouter outside the gateway without a
 *  word. Every line here is a fact the server reported (`chimera/providers/privacy.py` and the
 *  config blocks beside it), never a guess the screen makes; the controls live where they already
 *  are, and the lines say where.
 *
 *  Nothing here is a credential. The routes are provider NAMES, the egress list and the bot ids are
 *  statements the owner made and has to be able to read back. */
export function PrivacyPanel({
  privacy,
  config,
  t,
}: {
  privacy: Privacy;
  config: AppConfig;
  t: TFunc;
}) {
  const deny = privacy.openrouter_data_collection === "deny";
  const routes = privacy.routes ?? [];
  const unscoped = privacy.unscoped ?? [];
  const egress = config.autonomy.egress_allow ?? [];
  const bots = Object.entries(config.messaging?.allowed_users ?? {}).sort(([a], [b]) =>
    a.localeCompare(b),
  );
  const semantic = config.memory.semantic ?? false;
  return (
    <Panel title={t("governance.privacy.title")}>
      <p className="px-4 py-3 text-xs text-muted-foreground">{t("governance.privacy.blurb")}</p>

      <Section title={t("governance.privacy.routes")}>
        {routes.map((route) => (
          <div key={route.provider} className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-foreground">{route.provider}</span>
            <Badge tone={route.local ? "ok" : "muted"}>
              {route.local ? t("governance.privacy.local") : t("governance.privacy.remote")}
            </Badge>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
              {(route.roles ?? []).join(", ")}
            </span>
          </div>
        ))}
        {/* Sent on EVERY call, whatever the model slug says — so a list of providers without
            this line would name the wrong recipient for all of them. */}
        {config.models.api_base ? (
          <Line>{t("governance.privacy.apiBase", { url: config.models.api_base })}</Line>
        ) : null}
      </Section>

      <Section title={t("governance.privacy.retention")}>
        <Line>
          {deny ? t("governance.privacy.retentionDeny") : t("governance.privacy.retentionAllow")}
        </Line>
        {privacy.openrouter_zdr ? <Line>{t("governance.privacy.zdr")}</Line> : null}
        {/* Only matters once a preference is set: without one there is nothing this surface skips.
            `decisions_fallback` is the default install with an OpenRouter key — the common case,
            and the one the card used to miss. */}
        {(deny || privacy.openrouter_zdr) && unscoped.includes("decisions") ? (
          <span className="text-xs text-warn-foreground">{t("governance.privacy.unscoped")}</span>
        ) : null}
        {(deny || privacy.openrouter_zdr) && unscoped.includes("decisions_fallback") ? (
          <span className="text-xs text-warn-foreground">
            {t("governance.privacy.unscopedFallback")}
          </span>
        ) : null}
      </Section>

      <Section title={t("governance.privacy.memory")}>
        <Line>
          {t("governance.privacy.memoryChat", {
            state: config.memory.remember_from_chat
              ? t("governance.privacy.on")
              : t("governance.privacy.off"),
          })}
        </Line>
        <Line>
          {semantic
            ? t("governance.privacy.memorySemantic", { model: config.memory.embed_model ?? "" })
            : t("governance.privacy.memorySemanticOff")}
        </Line>
      </Section>

      <Section title={t("governance.privacy.telemetry")}>
        <Line>
          {privacy.telemetry ? t("governance.privacy.telemetryOn") : t("governance.privacy.telemetryOff")}
        </Line>
      </Section>

      <Section title={t("governance.privacy.egress")}>
        {egress.length ? (
          <span className="font-mono text-xs text-foreground">{egress.join(", ")}</span>
        ) : (
          <Line>{t("governance.privacy.egressNone")}</Line>
        )}
      </Section>

      <Section title={t("governance.privacy.bots")}>
        <div className="flex flex-wrap items-center gap-2">
          {bots.map(([platform, ids]) => (
            <Badge key={platform} tone={ids.length ? "muted" : "warn"}>
              {platform}:{" "}
              {ids.length
                ? t("governance.privacy.botListed", { n: ids.length })
                : t("governance.privacy.botOpen")}
            </Badge>
          ))}
        </div>
      </Section>
    </Panel>
  );
}
