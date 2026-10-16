import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Plug } from "lucide-react";

import { connectorState, toRetest, type ConnectorState } from "@/components/code/side/rules";
import { Button } from "@/components/ui/button";
import { getMcpServers, testMcpServer } from "@/lib/api";
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
export function ConnectorsCard() {
  const t = useT();
  const qc = useQueryClient();
  const side = useConversationSide();
  const servers = useQuery({ queryKey: ["mcp"], queryFn: getMcpServers, enabled: side !== null });
  const retest = useMutation({
    mutationFn: async (names: string[]) => {
      // One after the other: each test starts a server process, and starting several at once is
      // how a slow machine turns a passing server into a timed-out one.
      for (const name of names) await testMcpServer(name).catch(() => undefined);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["mcp"] }),
  });
  if (!side) return null;
  const list = servers.data?.servers ?? [];
  if (servers.isError) return <p className="text-sm text-muted-foreground">{t("side.mcp.unavailable")}</p>;
  if (list.length === 0) return servers.isLoading ? null : <p className="text-sm text-muted-foreground">{t("side.mcp.none")}</p>;
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
          <ConnectorRow key={server.name} server={server} />
        ))}
      </ul>
    </div>
  );
}

function ConnectorRow({ server }: { server: McpServer }) {
  const t = useT();
  const state = connectorState(server);
  const words: Record<ConnectorState, string> = {
    passed: t("side.mcp.state.passed", { n: server.last_test?.tool_count ?? 0 }),
    failed: t("side.mcp.state.failed"),
    held: t("side.mcp.state.held"),
    paused: t("mcp.disabled"),
    untested: t("side.mcp.state.untested"),
  };
  return (
    <li className="flex min-w-0 items-center gap-2 text-sm" data-state={state}>
      <Plug aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <span className="min-w-0 flex-1 truncate font-mono" title={server.name}>
        {server.name}
      </span>
      <span aria-hidden className={cn("h-2 w-2 shrink-0 rounded-full", DOT[state])} />
      <span className={cn("shrink-0 text-xs", state === "failed" ? "text-bad-foreground" : "text-muted-foreground")}>
        {words[state]}
      </span>
    </li>
  );
}
