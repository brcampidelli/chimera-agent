import { useEffect, useMemo, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getConfig, getConnectors, getMcpCatalog, getMcpServers, getPublicCatalog } from "@/lib/api";
import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { useRoute } from "@/lib/router";
import { useMcpControls } from "@/components/Mcp";
import { CatalogScreen } from "@/components/catalog/CatalogScreen";
import type { CatalogItem } from "@/components/catalog/model";
import { AddConnectorMenu } from "@/components/connectors/AddConnectorMenu";
import { ConnectorDetail, resolveConnector } from "@/components/connectors/ConnectorDetail";
import {
  CATEGORY_MCP,
  CATEGORY_OPENAPI,
  discoverSections,
  mcpItemId,
  mineSections,
  openapiItemId,
} from "@/components/connectors/connectorItems";

/**
 * Settings › Personalization › Connectors: MCP servers and OpenAPI connectors on one catalog screen
 * (study 04, §6, PR 6).
 *
 * Mine is what the owner configured, grouped by who has to act: a server whose tools changed since
 * they were approved, or one that still needs its browser sign-in, waits for the owner first. A card
 * only summarises; Test, the last test, the held diff and the sign-in live in the detail, addressed
 * by `item=` like a plugin's. Discover is the curated MCP catalogue — each card ends its capability
 * line with what actually limits that server — and then the public connectors catalogue, read from
 * a pinned commit. Nothing here is added by opening it: a curated entry fills a form in its detail,
 * and its secrets are typed there and never displayed.
 */
export function Connectors() {
  const { t, lang } = useI18n();
  const qc = useQueryClient();
  const { route, navigate, setParams } = useRoute();
  const params = route.params;
  const servers = useQuery({ queryKey: ["mcp"], queryFn: getMcpServers });
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const catalog = useQuery({ queryKey: ["mcp-catalog"], queryFn: getMcpCatalog });
  // An older backend has neither route; each one failing leaves its part of the screen empty, with
  // a line saying so, rather than taking the whole screen down.
  const connectors = useQuery({ queryKey: ["connectors"], queryFn: () => getConnectors(), retry: false });
  const publicCatalog = useQuery({
    queryKey: ["public-catalog", "connectors"],
    queryFn: () => getPublicCatalog("connectors"),
    retry: false,
  });
  const controls = useMcpControls();

  const serverList = useMemo(() => servers.data?.servers ?? [], [servers.data]);
  const connectorList = useMemo(() => connectors.data?.connectors ?? [], [connectors.data]);
  const entries = useMemo(() => catalog.data?.entries ?? [], [catalog.data]);
  const mine = useMemo(() => mineSections(serverList, connectorList, t), [serverList, connectorList, t]);
  const discover = useMemo(
    () => discoverSections(entries, serverList, publicCatalog.data, t),
    [entries, serverList, publicCatalog.data, t],
  );
  const byId = useMemo(() => new Map(serverList.map((s) => [mcpItemId(s.name), s])), [serverList]);

  const itemId = params.get("item");
  const target = resolveConnector(itemId, serverList, connectorList, entries, publicCatalog.data);
  // Settled, not just loaded: right after a save the server's own detail is opened while the list
  // is still being fetched again, and "no longer in the catalog" would flash for a server that is.
  const settled = !servers.isFetching && !catalog.isFetching && !connectors.isFetching && !publicCatalog.isFetching;
  const missing = itemId !== null && settled && target === null;

  // Whether the open panel's history entry is ours to step back over (the pattern of Plugins).
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
  const refreshConnectors = () => void qc.invalidateQueries({ queryKey: ["connectors"] });

  // A saved server: forget what this window saw for that name (it was about the server REPLACED),
  // run its test only when the owner pressed "Add and test", and show its own detail, where the
  // test's result is drawn.
  const onMcpAdded = (name: string, test: boolean) => {
    controls.forget(name);
    void controls.invalidate();
    if (test) void controls.runTest(name);
    const next: Record<string, string> = { ...Object.fromEntries(params), item: mcpItemId(name) };
    delete next.view;
    setParams(next);
    pushed.current = false;
  };

  if (servers.isError) return <ErrorState error={servers.error} onRetry={() => void servers.refetch()} />;

  const autoloadOff = config.data ? !config.data.mcp.autoload : false;
  const pub = publicCatalog.data;

  return (
    <div className="mx-auto max-w-3xl px-6 py-6">
      <CatalogScreen
        title={t("settings.tab.connectors")}
        subtitle={t("connectorsUi.subtitle")}
        searchLabel={t("connectorsUi.search")}
        mine={mine}
        discover={discover}
        params={params}
        onParamsChange={setParams}
        categoryLabel={(id) =>
          id === CATEGORY_MCP ? t("nav.mcp") : id === CATEGORY_OPENAPI ? t("connectors.tab") : id
        }
        addMenu={<AddConnectorMenu onMcpAdded={onMcpAdded} onConnectorAdded={refreshConnectors} />}
        addMenuInEmpty={false}
        loading={servers.isLoading || (params.get("view") === "discover" && catalog.isLoading)}
        labels={{ emptyMine: t("connectorsUi.empty") }}
        mineEmptyText={t("connectorsUi.emptyHint")}
        onOpen={open}
        onInstall={open}
        installLabel={(item) => t("connectorsUi.setUp", { name: item.name })}
        renderNote={(item) => {
          const server = byId.get(item.id);
          if (server) {
            if (server.manifest_held)
              return <p className="text-xs text-warn-foreground">{t("connectorsUi.note.held")}</p>;
            const live = controls.tests[server.name]?.result;
            if (live)
              return live.ok ? (
                <p className="text-xs text-ok-foreground">{t("mcp.connected", { n: live.tools.length })}</p>
              ) : (
                <p className="text-xs text-bad-foreground">{t("mcp.testFailed")}</p>
              );
            if (server.oauth && !server.oauth.connected)
              return <p className="text-xs text-muted-foreground">{t("mcp.oauth.needed")}</p>;
            // History, never the green "connected": a test from yesterday says nothing about today.
            const last = server.last_test;
            if (last) {
              const when = new Date(last.tested_at * 1000).toLocaleString(lang);
              return (
                <p className="text-xs text-muted-foreground">
                  {last.ok
                    ? t("mcp.lastTest.ok", { when, n: last.tool_count })
                    : t("mcp.lastTest.failed", { when })}
                </p>
              );
            }
            return null;
          }
          const problem = connectorList.find((c) => openapiItemId(c.name) === item.id)?.problem;
          return problem ? <p className="text-xs text-warn-foreground">{problem}</p> : null;
        }}
        discoverHeader={
          entries.length > 0 ? <p className="text-xs text-muted-foreground">{t("connectorsUi.discover.lead")}</p> : null
        }
        discoverFooter={
          pub ? (
            <p className="text-xs text-muted-foreground">
              {pub.available
                ? t("connectorsUi.public.from", { repo: pub.repo, sha: pub.sha.slice(0, 7) })
                : t("connectorsUi.public.unavailable", { reason: pub.reason ?? "—" })}
            </p>
          ) : null
        }
        notice={
          missing ? (
            <p role="status" className="flex items-center gap-2 text-xs text-warn-foreground">
              {t("catalogUi.itemMissing")}
              <Button size="sm" variant="ghost" onClick={() => setParams(withoutItem())}>
                {t("common.close")}
              </Button>
            </p>
          ) : connectors.isError ? (
            <p role="status" className="text-xs text-bad-foreground">
              {t("connectors.unavailable")}
            </p>
          ) : null
        }
      />
      <ConnectorDetail
        target={target}
        onClose={close}
        controls={controls}
        autoloadOff={autoloadOff}
        store={connectors.data?.store ?? null}
        onConnectorsChanged={refreshConnectors}
        onMcpAdded={onMcpAdded}
      />
    </div>
  );
}
