import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { getPlugins } from "@/lib/api";
import { Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";
import { AddPluginMenu } from "@/components/plugins/AddPlugin";
import { DiscoverPlugins } from "@/components/plugins/DiscoverPlugins";
import { PluginCard } from "@/components/plugins/PluginCard";

type Tab = "mine" | "discover";

function MyPlugins() {
  const t = useT();
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: getPlugins });
  if (plugins.isLoading) return <Spinner />;
  if (plugins.isError) return <ErrorState error={plugins.error} onRetry={() => void plugins.refetch()} />;
  const list = plugins.data?.plugins ?? [];
  if (list.length === 0) {
    return (
      <div className="space-y-1 py-6">
        <p className="text-sm text-foreground">{t("plugins.empty.mine")}</p>
        <p className="text-xs text-muted-foreground">{t("plugins.empty.mineHint")}</p>
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {list.map((plugin) => (
        <PluginCard key={plugin.name} plugin={plugin} />
      ))}
    </div>
  );
}

/**
 * Settings › Plugins (plan B6, the owner's decision to keep it in Settings like Claude's
 * "Personalização › Plugins", with no new rail icon).
 *
 * Claude Code plugins, read in Claude Code's own format. Two promises this screen keeps and says:
 * a third party's plugin arrives switched off, and only the owner switches it on — the switch opens
 * a confirmation that lists what will reach the agent and which servers, if any, to add. And no
 * number here is invented: there is no install count, because nothing on this machine measures one.
 */
export function Plugins() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>("mine");
  const items = [
    { value: "mine" as const, label: t("plugins.tab.mine") },
    { value: "discover" as const, label: t("plugins.tab.discover") },
  ];
  return (
    <div className="mx-auto max-w-3xl space-y-4 px-6 py-6">
      <p className="text-xs text-muted-foreground">{t("plugins.intro")}</p>
      <div className="flex items-end justify-between gap-2">
        <Tabs items={items} value={tab} onChange={setTab} aria-label={t("settings.tab.plugins")} className="flex-1" />
        <AddPluginMenu />
      </div>
      <TabPanel tabsId={id} value={tab}>
        {tab === "mine" ? <MyPlugins /> : <DiscoverPlugins />}
      </TabPanel>
    </div>
  );
}
