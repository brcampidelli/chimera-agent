import { useId, useRef } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT, type TFunc } from "@/lib/i18n";
import type { Plugin, PluginCapabilities } from "@/lib/types";
import { CapabilityLine } from "@/components/catalog/CapabilityLine";
import { CatalogDetail, DetailSteps } from "@/components/catalog/CatalogDetail";
import type { CatalogItem } from "@/components/catalog/model";
import { PluginDetailBody } from "@/components/plugins/PluginDetailBody";
import { usePluginToggle } from "@/components/plugins/PluginSwitch";
import { pluginItemId, type EntryRow } from "@/components/plugins/pluginItems";

export type DetailTarget = { kind: "plugin"; plugin: Plugin } | { kind: "entry"; row: EntryRow };

/**
 * What an `item=` in the URL points at, or `null` when it points at nothing that exists any more.
 * An entry that is already installed resolves to the installed plugin: its detail is the one with
 * the review and the switch.
 */
export function resolveTarget(
  id: string | null,
  plugins: readonly Plugin[],
  rows: ReadonlyMap<string, EntryRow>,
): DetailTarget | null {
  if (!id) return null;
  const row = rows.get(id);
  if (row) {
    const installed = row.entry.installed ? plugins.find((p) => p.name === row.entry.name) : undefined;
    return installed ? { kind: "plugin", plugin: installed } : { kind: "entry", row };
  }
  const plugin = plugins.find((p) => pluginItemId(p.name) === id);
  return plugin ? { kind: "plugin", plugin } : null;
}

const RUN_KEYS: readonly [keyof PluginCapabilities, string][] = [
  ["process", "plugins.runs.process"],
  ["network", "plugins.runs.network"],
  ["exec", "plugins.runs.exec"],
];

/** The kinds of thing an entry runs, from its counts — or that we do not know before installing. */
function runsWords(caps: PluginCapabilities | null | undefined, t: TFunc): string {
  if (!caps) return t("plugins.cap.unknown");
  const kinds = RUN_KEYS.filter(([k]) => (caps[k] ?? 0) > 0).map(([, key]) => t(key));
  return kinds.length > 0 ? kinds.join(" · ") : t("plugins.runs.text");
}

function EntryBody({ row }: { row: EntryRow }) {
  const t = useT();
  const { entry, item } = row;
  return (
    <div className="flex flex-col gap-3">
      {entry.description ? <p className="text-sm text-foreground">{entry.description}</p> : null}
      <CapabilityLine facts={item.capabilities} />
      <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-xs">
        <dt className="text-muted-foreground">{t("plugins.filter.category")}</dt>
        <dd className="col-span-2">{entry.category || t("plugins.category.none")}</dd>
        <dt className="text-muted-foreground">{t("plugins.filter.runs")}</dt>
        <dd className="col-span-2">{runsWords(entry.capabilities, t)}</dd>
        <dt className="text-muted-foreground">{t("plugins.detail.source")}</dt>
        <dd className="col-span-2 break-all font-mono">{[row.market, item.source].filter(Boolean).join(" · ")}</dd>
      </dl>
    </div>
  );
}

function EntrySteps({
  row,
  onInstall,
  installing,
  installError,
}: {
  row: EntryRow;
  onInstall: (item: CatalogItem) => void;
  installing: boolean;
  installError: string | null;
}) {
  const t = useT();
  const reasonId = useId();
  const { item } = row;
  const reason = item.installable ? null : item.refusal;
  return (
    <DetailSteps
      steps={[
        {
          id: "install",
          label: t("catalogUi.step.install"),
          state: "current",
          note: reason ? (
            <span id={reasonId} className="text-warn-foreground">
              {reason}
            </span>
          ) : installError ? (
            <span className="text-bad-foreground">{installError}</span>
          ) : null,
          action: (
            <Button
              size="sm"
              disabled={!item.installable || installing}
              aria-label={t("plugins.install", { name: item.name })}
              aria-describedby={reason ? reasonId : undefined}
              onClick={() => onInstall(item)}
            >
              {installing ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
              {t("catalogUi.installButton")}
            </Button>
          ),
        },
        { id: "review", label: t("catalogUi.step.review"), state: "todo" },
        { id: "enable", label: t("catalogUi.step.enable"), state: "todo" },
      ]}
    />
  );
}

function InstalledSteps({ plugin }: { plugin: Plugin }) {
  const t = useT();
  const toggle = usePluginToggle(plugin);
  const status = plugin.status ?? "pending";
  const waiting = status === "pending" || status === "held";
  return (
    <>
      <DetailSteps
        steps={[
          { id: "install", label: t("catalogUi.step.install"), state: "done", note: t("plugins.installed") },
          {
            id: "review",
            label: t("catalogUi.step.review"),
            state: plugin.denied ? "todo" : waiting ? "current" : "done",
            note: plugin.denied ? t("plugins.status.denied") : null,
            action:
              waiting && !plugin.denied ? (
                <Button size="sm" onClick={toggle.review}>
                  {t("plugins.review")}
                </Button>
              ) : null,
          },
          {
            id: "enable",
            label: t("catalogUi.step.enable"),
            state: plugin.denied || waiting ? "todo" : status === "active" ? "done" : "current",
            note: toggle.error,
            action: toggle.control,
          },
        ]}
      />
      {toggle.dialog}
    </>
  );
}

/**
 * The plugin's detail panel: one `CatalogDetail`, whatever it shows, so installing from an entry's
 * panel turns it into the installed plugin's panel without closing and reopening under the person.
 * It keeps the last target while it animates out, so the panel does not empty as it leaves.
 */
export function PluginDetail({
  target,
  onClose,
  onInstall,
  installingId,
  installError,
}: {
  target: DetailTarget | null;
  onClose: () => void;
  onInstall: (item: CatalogItem) => void;
  installingId: string | null;
  installError: { id: string; text: string } | null;
}) {
  const t = useT();
  const last = useRef<DetailTarget | null>(target);
  if (target) last.current = target;
  const shown = last.current;
  if (!shown) return null;

  const plugin = shown.kind === "plugin" ? shown.plugin : null;
  const row = shown.kind === "entry" ? shown.row : null;
  const title = plugin ? plugin.display_name || plugin.name : (row?.item.name ?? "");
  const author = plugin?.author || null;
  const subtitle = [author ? t("catalogUi.by", { author }) : "", plugin?.license ?? "", row?.market ?? ""]
    .filter(Boolean)
    .join(" · ");

  return (
    <CatalogDetail
      open={target !== null}
      onClose={onClose}
      title={title}
      subtitle={subtitle || undefined}
      footer={
        plugin ? (
          <InstalledSteps key={plugin.name} plugin={plugin} />
        ) : row ? (
          <EntrySteps
            row={row}
            onInstall={onInstall}
            installing={installingId === row.item.id}
            installError={installError?.id === row.item.id ? installError.text : null}
          />
        ) : null
      }
    >
      {plugin ? <PluginDetailBody plugin={plugin} onRemoved={onClose} /> : row ? <EntryBody row={row} /> : null}
    </CatalogDetail>
  );
}
