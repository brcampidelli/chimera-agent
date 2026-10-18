import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";

import { getPlugins } from "@/lib/api";
import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { useRoute } from "@/lib/router";
import { CatalogScreen } from "@/components/catalog/CatalogScreen";
import type { CatalogItem } from "@/components/catalog/model";
import { AddPluginMenu } from "@/components/plugins/AddPlugin";
import { useDiscoverPlugins } from "@/components/plugins/DiscoverPlugins";
import { PluginDetail, resolveTarget } from "@/components/plugins/PluginDetail";
import { PluginSwitch } from "@/components/plugins/PluginSwitch";
import { mineSections, NO_CATEGORY, pluginItemId } from "@/components/plugins/pluginItems";

/**
 * Settings › Plugins (plan B6, the owner's decision to keep it in Settings like Claude's
 * "Personalização › Plugins", with no new rail icon), drawn on the shared catalog shell (study 04,
 * PRs 3 and 4).
 *
 * Claude Code plugins, read in Claude Code's own format. Two promises this screen keeps and says:
 * a third party's plugin arrives switched off, and only the owner switches it on — the switch opens
 * a confirmation that lists what will reach the agent and which servers, if any, to add. And no
 * number here is invented: there is no install count, because nothing on this machine measures one.
 *
 * The detail panel lives in the URL as `item=`. Opening it PUSHES a history entry, so the browser's
 * Back closes it; closing it from the panel steps back over that same entry instead of leaving a
 * dead one behind. A link whose item no longer exists opens the screen with one line saying so.
 */
export function Plugins() {
  const t = useT();
  const { route, navigate, setParams } = useRoute();
  const params = route.params;
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: getPlugins });
  const discover = useDiscoverPlugins();
  const list = plugins.data?.plugins ?? [];

  const itemId = params.get("item");
  const target = resolveTarget(itemId, list, discover.rows);
  const loaded = !plugins.isLoading && !discover.loading;
  const missing = itemId !== null && loaded && target === null;
  const byId = new Map(list.map((p) => [pluginItemId(p.name), p]));

  // Whether the open panel's history entry is ours to step back over.
  const pushed = useRef(false);
  useEffect(() => {
    if (!itemId) pushed.current = false;
  }, [itemId]);

  const withoutItem = () => {
    const next = Object.fromEntries(params);
    delete next.item;
    return next;
  };
  const open = (item: CatalogItem) => {
    navigate(route.view, { ...Object.fromEntries(params), item: item.id });
    pushed.current = true;
  };
  const close = () => {
    if (pushed.current) {
      pushed.current = false;
      window.history.back();
    } else {
      setParams(withoutItem());
    }
  };

  if (plugins.isError) return <ErrorState error={plugins.error} onRetry={() => void plugins.refetch()} />;

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <CatalogScreen
        title={t("settings.tab.plugins")}
        subtitle={t("plugins.intro")}
        searchLabel={t("plugins.search")}
        mine={mineSections(list, t)}
        discover={discover.sections}
        params={params}
        onParamsChange={setParams}
        categoryLabel={(id) => (id === NO_CATEGORY ? t("plugins.category.none") : id)}
        addMenu={<AddPluginMenu />}
        addMenuInEmpty={false}
        loading={plugins.isLoading || (params.get("view") === "discover" && discover.loading)}
        labels={{
          mine: t("plugins.tab.mine"),
          discover: t("plugins.tab.discover"),
          emptyMine: t("plugins.empty.mine"),
          noMatchFilters: t("plugins.noMatch"),
        }}
        mineEmptyText={t("plugins.empty.mineHint")}
        onOpen={open}
        onInstall={discover.install}
        installingId={discover.installingId}
        installLabel={(item) => t("plugins.install", { name: item.name })}
        renderAction={(item) => {
          const plugin = byId.get(item.id);
          return plugin ? <PluginSwitch plugin={plugin} /> : null;
        }}
        renderNote={(item) => {
          if (discover.installError?.id === item.id)
            return <p className="text-xs text-bad-foreground">{discover.installError.text}</p>;
          const diff = byId.get(item.id)?.held_diff;
          if (item.status !== "held" || !diff) return null;
          return (
            <p className="text-xs text-warn-foreground">
              {t("plugins.diff.files", {
                added: (diff.files_added ?? []).length,
                removed: (diff.files_removed ?? []).length,
                changed: (diff.files_changed ?? []).length,
              })}
            </p>
          );
        }}
        discoverHeader={discover.header}
        discoverFooter={discover.footer}
        notice={
          missing ? (
            <p role="status" className="flex items-center gap-2 text-xs text-warn-foreground">
              {t("catalogUi.itemMissing")}
              <Button size="sm" variant="ghost" onClick={() => setParams(withoutItem())}>
                {t("common.close")}
              </Button>
            </p>
          ) : null
        }
      />
      <PluginDetail
        target={target}
        onClose={close}
        onInstall={discover.install}
        installingId={discover.installingId}
        installError={discover.installError}
      />
    </div>
  );
}
