import type { ReactNode } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";

import {
  getPluginMarketplaceEntries,
  getPluginMarketplaces,
  installPlugin,
  removePluginMarketplace,
} from "@/lib/api";
import { ErrorState } from "@/components/ui/async";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { CatalogSectionSpec } from "@/components/catalog/CatalogScreen";
import type { CatalogItem } from "@/components/catalog/model";
import { discoverSections, fromMarketEntry, type EntryRow } from "@/components/plugins/pluginItems";

const iconButton = cn(
  "rounded-md p-1 text-muted-foreground transition-colors duration-1 ease-out hover:text-foreground",
  focusRing,
);

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export interface DiscoverPlugins {
  sections: CatalogSectionSpec[];
  /** Every entry, by catalog item id — what the detail panel and the "+" resolve against. */
  rows: ReadonlyMap<string, EntryRow>;
  /** The marketplace list itself, or any marketplace's entries, still loading. */
  loading: boolean;
  header: ReactNode;
  footer: ReactNode;
  install: (item: CatalogItem) => void;
  installingId: string | null;
  /** The last install that failed, and why — drawn on that item's card. */
  installError: { id: string; text: string } | null;
}

/**
 * Discover as an adapter (study 04, PR 3): the marketplaces the owner added, read entry by entry
 * into the catalog's neutral item, in honest sections. An entry that cannot be installed — an
 * unpinned source, a kind v1 does not fetch — stays listed with the reason and its "+" disabled:
 * hiding it would make "this marketplace lists twelve" read as "this marketplace lists eight".
 *
 * "+" installs PENDING. Installing is an operation the bridge may run; switching on is not, and
 * it is never reachable from here.
 */
export function useDiscoverPlugins(): DiscoverPlugins {
  const t = useT();
  const client = useQueryClient();
  const markets = useQuery({ queryKey: ["plugin-markets"], queryFn: getPluginMarketplaces });
  const marketList = markets.data?.marketplaces ?? [];
  const names = marketList.map((m) => m.name);
  const lists = useQueries({
    queries: names.map((name) => ({
      queryKey: ["plugin-market", name],
      queryFn: () => getPluginMarketplaceEntries(name),
    })),
  });
  const forget = useMutation({
    mutationFn: (name: string) => removePluginMarketplace(name),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["plugin-markets"] }),
  });
  const install = useMutation({
    mutationFn: (row: EntryRow) => installPlugin({ plugin: row.entry.name, marketplace: row.market }),
    onSuccess: (_data, row) => {
      void client.invalidateQueries({ queryKey: ["plugins"] });
      void client.invalidateQueries({ queryKey: ["plugin-market", row.market] });
    },
  });

  const rows = new Map<string, EntryRow>();
  for (const q of lists) {
    const data = q.data;
    if (!data) continue;
    const market = marketList.find((m) => m.name === data.marketplace);
    for (const entry of data.plugins ?? []) {
      const item = fromMarketEntry(market, data.marketplace, entry, t);
      rows.set(item.id, { market: data.marketplace, entry, item });
    }
  }
  const sections = discoverSections([...rows.values()], names, t);

  const failed = lists
    .map((q, i) => ({ name: names[i], error: q.error }))
    .filter((f): f is { name: string; error: Error } => !!f.error);

  const header = markets.isError ? (
    <ErrorState error={markets.error} onRetry={() => void markets.refetch()} />
  ) : (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">{t("plugins.discover.curatedNone")}</p>
      {!markets.isLoading && names.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("plugins.discover.noMarkets")}</p>
      ) : null}
      {failed.map((f) => (
        <p key={f.name} className="text-xs text-bad-foreground">
          {t("plugins.discover.marketError", { name: f.name, reason: errorText(f.error) })}
        </p>
      ))}
    </div>
  );

  // The marketplaces come last (§2.4): where the items come from, with the pin and a way to forget one.
  const footer =
    marketList.length === 0 ? null : (
      <div className="space-y-2">
        <h3 className="text-sm font-semibold">{t("plugins.discover.markets")}</h3>
        <ul className="flex flex-wrap gap-2" aria-label={t("plugins.discover.markets")}>
          {marketList.map((m) => (
            <li key={m.name} className="flex items-center gap-1 rounded-chip bg-surface-2 px-2 py-0.5 text-xs ring-1 ring-hairline">
              <span className="font-mono">{m.name}</span>
              <span className="text-muted-foreground">{m.kind === "github" ? `${m.repo}@${m.sha.slice(0, 7)}` : m.path}</span>
              <button
                type="button"
                className={iconButton}
                aria-label={t("plugins.market.remove", { name: m.name })}
                onClick={() => forget.mutate(m.name)}
              >
                <X aria-hidden className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
        </ul>
        {forget.error ? <p className="text-xs text-bad-foreground">{errorText(forget.error)}</p> : null}
      </div>
    );

  return {
    sections,
    rows,
    loading: markets.isLoading || lists.some((q) => q.isLoading),
    header,
    footer,
    install: (item) => {
      const row = rows.get(item.id);
      if (row && row.entry.installable) install.mutate(row);
    },
    installingId: install.isPending && install.variables ? install.variables.item.id : null,
    installError:
      install.error && install.variables ? { id: install.variables.item.id, text: errorText(install.error) } : null,
  };
}
