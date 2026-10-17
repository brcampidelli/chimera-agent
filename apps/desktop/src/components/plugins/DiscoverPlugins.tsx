import { useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plus, Search, X } from "lucide-react";

import {
  getPluginMarketplaceEntries,
  getPluginMarketplaces,
  installPlugin,
  removePluginMarketplace,
} from "@/lib/api";
import { Badge, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { PluginMarketplaceEntry } from "@/lib/types";
import { CapabilityLine, PluginIcon, StatusBadge } from "@/components/plugins/PluginParts";
import { factsOfCounts, runsMatch, type RunsFilter } from "@/components/plugins/capabilities";

const RUNS: readonly { value: RunsFilter; key: string }[] = [
  { value: "text", key: "plugins.runs.text" },
  { value: "process", key: "plugins.runs.process" },
  { value: "network", key: "plugins.runs.network" },
  { value: "exec", key: "plugins.runs.exec" },
];

const iconButton = cn(
  "rounded-md p-1 text-muted-foreground transition-colors duration-1 ease-out hover:text-foreground",
  focusRing,
);

/** One entry of a marketplace. The "+" installs it PENDING; an entry whose source is unpinned or of
 *  a kind v1 does not fetch stays visible with the reason, its button disabled — hiding it would
 *  make "this marketplace lists twelve" read as "this marketplace lists eight". */
function EntryCard({ market, entry }: { market: string; entry: PluginMarketplaceEntry }) {
  const t = useT();
  const client = useQueryClient();
  const install = useMutation({
    mutationFn: () => installPlugin({ plugin: entry.name, marketplace: market }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["plugins"] });
      void client.invalidateQueries({ queryKey: ["plugin-market", market] });
    },
  });
  const why = entry.installable ? "" : t("plugins.notInstallable", { reason: entry.refusal || "—" });
  return (
    <article className="flex items-start gap-3 py-3" aria-label={entry.name}>
      <PluginIcon />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h4 className="text-sm font-semibold">{entry.name}</h4>
          {entry.category ? <Badge>{entry.category}</Badge> : null}
          {entry.installed ? <StatusBadge status={entry.installed} /> : null}
        </div>
        {entry.description ? <p className="text-xs text-muted-foreground">{entry.description}</p> : null}
        <p className="text-xs text-muted-foreground">{market}</p>
        <CapabilityLine facts={factsOfCounts(entry.capabilities)} />
        {why ? <p className="text-xs text-warn-foreground">{why}</p> : null}
        {install.error ? (
          <p className="text-xs text-bad-foreground">
            {install.error instanceof Error ? install.error.message : String(install.error)}
          </p>
        ) : null}
      </div>
      {entry.installed ? (
        <span className="text-xs text-muted-foreground">{t("plugins.installed")}</span>
      ) : (
        <button
          type="button"
          className={cn(iconButton, "disabled:cursor-not-allowed disabled:opacity-50")}
          disabled={!entry.installable || install.isPending}
          aria-label={t("plugins.install", { name: entry.name })}
          title={why || t("plugins.install", { name: entry.name })}
          onClick={() => install.mutate()}
        >
          {install.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
        </button>
      )}
    </article>
  );
}

export function DiscoverPlugins() {
  const t = useT();
  const client = useQueryClient();
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [runs, setRuns] = useState<RunsFilter>("");
  const markets = useQuery({ queryKey: ["plugin-markets"], queryFn: getPluginMarketplaces });
  const names = (markets.data?.marketplaces ?? []).map((m) => m.name);
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

  const rows = lists.flatMap((q) => (q.data?.plugins ?? []).map((entry) => ({ market: q.data?.marketplace ?? "", entry })));
  const categories = [...new Set(rows.map((r) => r.entry.category).filter((c): c is string => !!c))].sort();
  const needle = query.trim().toLowerCase();
  const shown = rows.filter(({ entry }) => {
    const text = [entry.name, entry.description ?? "", ...(entry.tags ?? [])].join(" ").toLowerCase();
    return (
      (!needle || text.includes(needle)) &&
      (!category || entry.category === category) &&
      runsMatch(factsOfCounts(entry.capabilities), runs)
    );
  });

  if (markets.isLoading) return <Spinner />;
  if (markets.isError) return <ErrorState error={markets.error} onRetry={() => void markets.refetch()} />;

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">{t("plugins.discover.curatedNone")}</p>
      {names.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("plugins.discover.noMarkets")}</p>
      ) : (
        <>
          <ul className="flex flex-wrap gap-2" aria-label={t("plugins.discover.markets")}>
            {(markets.data?.marketplaces ?? []).map((m) => (
              <li key={m.name} className="flex items-center gap-1 rounded-chip bg-surface-2 px-2 py-0.5 text-xs ring-1 ring-hairline">
                <span className="font-mono">{m.name}</span>
                <span className="text-muted-foreground">{m.kind === "github" ? `${m.repo}@${m.sha.slice(0, 7)}` : m.path}</span>
                <button
                  type="button"
                  className={iconButton}
                  aria-label={t("plugins.market.remove", { name: m.name })}
                  onClick={() => forget.mutate(m.name)}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
          <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
            <label className="field flex h-8 min-w-0 flex-1 items-center gap-1.5 px-2">
              <Search aria-hidden className="h-3.5 w-3.5 shrink-0" />
              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={t("plugins.search")}
                aria-label={t("plugins.search")}
                className="min-w-0 flex-1 bg-transparent text-sm text-foreground outline-hidden placeholder:text-muted-foreground"
              />
            </label>
            <label className="flex items-center gap-1">
              {t("plugins.filter.category")}
              <select className="field h-7 px-2 text-xs" value={category} onChange={(e) => setCategory(e.target.value)}>
                <option value="">{t("catalog.filter.all")}</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1">
              {t("plugins.filter.runs")}
              <select
                className="field h-7 px-2 text-xs"
                value={runs}
                onChange={(e) => setRuns((RUNS.find((r) => r.value === e.target.value)?.value ?? "") as RunsFilter)}
              >
                <option value="">{t("catalog.filter.all")}</option>
                {RUNS.map((r) => (
                  <option key={r.value} value={r.value}>
                    {t(r.key)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {lists.some((q) => q.isLoading) ? <Spinner /> : null}
          {shown.length === 0 && !lists.some((q) => q.isLoading) ? (
            <p className="text-sm text-muted-foreground">{t("plugins.noMatch")}</p>
          ) : (
            <div className="surface divide-y divide-hairline px-4">
              {shown.map(({ market, entry }) => (
                <EntryCard key={`${market}/${entry.name}`} market={market} entry={entry} />
              ))}
            </div>
          )}
        </>
      )}
      {forget.error ? (
        <p className="text-xs text-bad-foreground">
          {forget.error instanceof Error ? forget.error.message : String(forget.error)}
        </p>
      ) : null}
    </div>
  );
}
