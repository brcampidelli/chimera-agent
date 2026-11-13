import { useQuery } from "@tanstack/react-query";

import { Badge, Panel } from "@/components/ui/panel";
import { getSecurityOverview } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";
import type { SafetyOverview } from "@/lib/types";

/**
 * One card for what holds on this machine right now: what still comes back to the owner with
 * "Ignore permissions" on, where commands really run, the checks each place runs, and which switches
 * are on. Every value comes from `POST /api/security/overview`, which computes it with the functions
 * the server decides with (`chimera/api/posture.py`, `safety_overview`). Nothing here is a second,
 * hand-kept table of the rules: a label is looked up by the code the server sends, and a code this
 * build does not know is shown as sent rather than dropped.
 */
export function SafetyOverviewCard() {
  const t = useT();
  const card = useQuery({
    queryKey: ["security-overview"],
    queryFn: () => getSecurityOverview(),
    // Never served from a cache: a sandbox that stopped answering must change the card.
    staleTime: 0,
    retry: false,
  });
  if (card.isError) {
    return (
      <Panel title={t("settings.safety.title")}>
        <p role="alert" className="px-4 py-3 text-sm text-bad-foreground">
          {t("settings.safety.error")}
        </p>
      </Panel>
    );
  }
  const data = card.data;
  if (!data) return null;
  return (
    <Panel title={t("settings.safety.title")}>
      <FloorSection data={data} />
      <SandboxSection data={data} />
      <GovernanceSection data={data} />
      <BypassSection data={data} />
    </Panel>
  );
}

function label(t: ReturnType<typeof useT>, key: string, fallback: string): string {
  const text = t(key);
  return text === key ? fallback : text;
}

function FloorSection({ data }: { data: SafetyOverview }) {
  const t = useT();
  return (
    <div className="px-4 py-3 text-sm">
      <h3 className="font-semibold">{t("settings.safety.floor")}</h3>
      <ul className="mt-1.5 list-disc space-y-0.5 pl-5" aria-label={t("settings.safety.floor")}>
        {data.floor.map((category) => (
          <li key={category}>
            {label(t, `settings.safety.floor.${category}`, category)}
            {data.floor_lexical.includes(category) && (
              <span className="text-muted-foreground"> {t("settings.safety.floor.lexical")}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SandboxSection({ data }: { data: SafetyOverview }) {
  const t = useT();
  const fellBack = !data.sandbox_isolated && data.sandbox_configured !== "local";
  return (
    <div className="px-4 py-3 text-sm">
      <h3 className="font-semibold">{t("settings.safety.sandbox")}</h3>
      <p className="mt-1">
        {data.sandbox_isolated ? (
          <Badge tone="ok">{t("settings.safety.sandbox.isolated", { name: data.sandbox })}</Badge>
        ) : (
          <Badge tone="warn">{t("settings.safety.sandbox.host")}</Badge>
        )}
      </p>
      {fellBack && (
        <p role="status" className="mt-1 text-xs text-warn-foreground">
          {t("settings.safety.sandbox.fellBack", { asked: data.sandbox_configured })}
        </p>
      )}
    </div>
  );
}

function GovernanceSection({ data }: { data: SafetyOverview }) {
  const t = useT();
  return (
    <div className="px-4 py-3 text-sm">
      <h3 className="font-semibold">{t("settings.safety.governance")}</h3>
      <ul className="mt-1.5 space-y-1">
        {data.governance.map((row) => (
          <li key={row.surface} className="flex flex-wrap items-center gap-2">
            <span className="min-w-32 font-medium">
              {label(t, `settings.safety.surface.${row.surface}`, row.surface)}
            </span>
            <Badge tone={row.kernel === "enforce" ? "ok" : row.kernel === "observe" ? "accent" : "muted"}>
              {label(t, `settings.safety.kernel.${row.kernel}`, row.kernel)}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {row.taint ? t("settings.safety.watch.on") : t("settings.safety.watch.off")}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function BypassSection({ data }: { data: SafetyOverview }) {
  const t = useT();
  const { lang } = useI18n();
  const when = (seconds: number) =>
    new Intl.DateTimeFormat(lang, { dateStyle: "short", timeStyle: "short" }).format(new Date(seconds * 1000));
  return (
    <div className="px-4 py-3 text-sm">
      <h3 className="font-semibold">{t("settings.safety.bypass.title")}</h3>
      {data.bypass.length === 0 ? (
        <p className="mt-1 text-muted-foreground">{t("settings.safety.bypass.none")}</p>
      ) : (
        <ul className="mt-1.5 space-y-0.5" aria-label={t("settings.safety.bypass.title")}>
          {data.bypass.map((row) => (
            <li key={`${row.scope}:${row.id}`} className="break-all">
              {row.scope === "project"
                ? t("settings.safety.bypass.project", { id: row.id })
                : t("settings.safety.bypass.conversation", { id: row.id })}{" "}
              <span className="text-muted-foreground">
                {row.expires_at != null
                  ? t("settings.safety.bypass.until", { when: when(row.expires_at) })
                  : t("settings.safety.bypass.noExpiry")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
