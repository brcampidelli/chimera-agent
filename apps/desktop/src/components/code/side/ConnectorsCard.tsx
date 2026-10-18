import { useMutation, useQuery } from "@tanstack/react-query";
import { ExternalLink, Loader2, Plug } from "lucide-react";

import { useMcpControls } from "@/components/Mcp";
import { connectorState, toRetest, type ConnectorState } from "@/components/code/side/rules";
import { Button } from "@/components/ui/button";
import { getMcpServers } from "@/lib/api";
import { useConversationSide } from "@/lib/conversation-side";
import { useT } from "@/lib/i18n";
import type { McpServer } from "@/lib/types";
import { cn } from "@/lib/utils";

const DOT: Record<ConnectorState, string> = {
  passed: "bg-ok",
  failed: "bg-bad",
  held: "bg-warn",
  paused: "bg-muted-foreground",
  untested: "bg-muted-foreground",
};

/**
 * The MCP servers the conversation's turns are offered (C5), with the state each is in.
 *
 * The servers are configured for the installation, not per folder, so every conversation lists the
 * same ones. The list is the one Settings reads (same query key) and is never polled: it changes when
 * someone adds, removes or tests a server, and each of those refreshes it.
 *
 * "Test again" is the only connecting action the app has (`POST /api/mcp/{name}/test`), and it says
 * what it is: a test, remembered on disk, which is history and not a claim that the server is
 * connected now. A running conversation's servers connect when the app starts; nothing here
 * reconnects them, and a label saying so would promise what the button cannot do.
 */
export function ConnectorsCard({ alone = false }: { alone?: boolean } = {}) {
  const t = useT();
  const facts = useConnectorServers();
  const { servers } = facts;
  const controls = useMcpControls();
  const retest = useMutation({
    mutationFn: async (names: string[]) => {
      // One after the other: each test starts a server process, and starting several at once is
      // how a slow machine turns a passing server into a timed-out one.
      for (const name of names) await controls.runTest(name);
    },
  });
  const list = servers.data?.servers ?? [];
  // No server configured is nothing to show: the dock leaves the card out, and only a card drawn where
  // it cannot vanish (`alone`) says so. A configured server, tested or not, is always shown.
  if (alone && facts.side !== null && !servers.isError && !servers.isLoading && list.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("side.mcp.none")}</p>;
  }
  if (connectorsSilent(facts)) return null;
  if (servers.isError) return <p className="text-sm text-muted-foreground">{t("side.mcp.unavailable")}</p>;
  const failed = toRetest(list);
  return (
    <div className="space-y-2" data-testid="side-connectors">
      {failed.length > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-bad-foreground">{t("side.mcp.toRetest", { n: failed.length })}</span>
          <Button
            size="sm"
            variant="outline"
            disabled={retest.isPending}
            onClick={() => retest.mutate(failed.map((s) => s.name))}
          >
            {retest.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {t("side.mcp.retest")}
          </Button>
        </div>
      ) : null}
      <ul className="space-y-1">
        {list.map((server) => (
          <ConnectorRow key={server.name} server={server} controls={controls} />
        ))}
      </ul>
    </div>
  );
}

/** What the card draws from, shared with the dock's silence rule (`useSilentPanels`) so the two read
 *  the same value. `enabled: false` asks nothing, for a card the person closed. */
export function useConnectorServers(enabled = true) {
  const side = useConversationSide();
  const servers = useQuery({ queryKey: ["mcp"], queryFn: getMcpServers, enabled: enabled && side !== null });
  return { side, servers };
}

/** The card draws nothing in a dock: no conversation, or no server configured (or the list not read
 *  yet). A failed read is something to say ("unavailable"), and so is any configured server, tested or
 *  not: an untested server is the person's to test. */
export function connectorsSilent({ side, servers }: ReturnType<typeof useConnectorServers>): boolean {
  if (side === null) return true;
  return !servers.isError && (servers.data?.servers ?? []).length === 0;
}

function ConnectorRow({ server, controls }: { server: McpServer; controls: ReturnType<typeof useMcpControls> }) {
  const t = useT();
  const state = connectorState(server);
  const words: Record<ConnectorState, string> = {
    passed: t("side.mcp.state.passed", { n: server.last_test?.tool_count ?? 0 }),
    failed: t("side.mcp.state.failed"),
    held: t("side.mcp.state.held"),
    paused: t("mcp.disabled"),
    untested: t("side.mcp.state.untested"),
  };
  // A sign-in server answers first for the sign-in, which is what the person can act on.
  const statusWord = !server.oauth
    ? words[state]
    : server.oauth.connected
      ? t("side.mcp.oauth.connected")
      : t("side.mcp.oauth.expired");
  return (
    <li className="flex min-w-0 items-center gap-2 text-sm" data-state={state}>
      <Plug aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate font-mono" title={server.name}>
        {server.name}
      </span>
      <span aria-hidden className={cn("h-2 w-2 shrink-0 rounded-full", DOT[state])} />
      <span className={cn("shrink-0 text-xs", state === "failed" ? "text-bad-foreground" : "text-muted-foreground")}>
        {statusWord}
      </span>
      {state === "untested" ? (
        <Button size="sm" variant="ghost" disabled={Boolean(controls.tests[server.name]?.loading)} onClick={() => void controls.runTest(server.name)}>
          {controls.tests[server.name]?.loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {t("mcp.test")}
        </Button>
      ) : null}
      {server.oauth && !server.oauth.connected ? (
        <Button size="sm" variant="outline" disabled={controls.signIns[server.name]?.loading} onClick={() => void controls.runSignIn(server.name, true)}>
          {controls.signIns[server.name]?.loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {t("side.mcp.oauth.reconnect")}
        </Button>
      ) : null}
      {controls.signIns[server.name]?.loading ? <span className="text-xs text-muted-foreground">{t("mcp.oauth.waiting")}</span> : null}
      {controls.signIns[server.name]?.error ? (
        <span className="text-xs text-bad-foreground">{t("mcp.oauth.error.other", { n: controls.signIns[server.name]?.error ?? "" })}</span>
      ) : null}
      <a className="inline-flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-surface-hover hover:text-foreground" href="#/settings?tab=connectors" aria-label={t("side.mcp.settings", { name: server.name })}>
        <ExternalLink aria-hidden className="h-3.5 w-3.5" />
      </a>
    </li>
  );
}
