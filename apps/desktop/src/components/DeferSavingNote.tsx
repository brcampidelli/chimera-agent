import { useQuery } from "@tanstack/react-query";

import { getDeferSaving } from "@/lib/api";
import { useNum, useT } from "@/lib/i18n";
import type { TFunc } from "@/lib/i18n";
import type { DeferSaving } from "@/lib/types";

/**
 * The sentence under a deferral switch: what it would save on THIS install, measured by the server
 * (`GET /api/tools/defer-saving`), never a figure quoted from somebody else's harness.
 *
 * A loss is said as a loss. Below a handful of tools the three lookup proxies cost more than what
 * they replace, and a note that only ever showed savings would be the switch arguing for itself.
 * When there is no MCP figure the note says why — autoload off, not connected yet, or nothing
 * connected — because each asks something different of the owner.
 */
export function deferSavingText(
  data: DeferSaving,
  half: "builtin" | "mcp",
  t: TFunc,
  num: (n: number) => string,
): string {
  const figure = half === "builtin" ? data.builtin : data.mcp;
  if (!figure) {
    if (data.mcp_state === "autoload_off") return t("settings.defer.autoloadOff");
    if (data.mcp_state === "not_connected") return t("settings.defer.notConnected");
    // A server that hung on its tool listing. The built-in note above still has its number: the
    // server reports this half's failure instead of failing the whole request.
    if (data.mcp_state === "unavailable") return t("settings.defer.mcpUnavailable");
    return t("settings.defer.noServers");
  }
  const params = {
    from: num(figure.declared_chars),
    to: num(figure.deferred_chars),
    pct: num(Math.round(Math.abs(figure.saving_pct))),
  };
  return figure.saving_pct < 0
    ? t("settings.defer.loss", params)
    : t("settings.defer.saving", params);
}

export function DeferSavingNote({ half }: { half: "builtin" | "mcp" }) {
  const t = useT();
  const num = useNum();
  // One request for both notes: they share the key, and the answer does not depend on either switch.
  const saving = useQuery({
    queryKey: ["defer-saving"],
    queryFn: () => getDeferSaving(),
    retry: false,
  });
  if (saving.isPending) return null;
  const text = saving.data
    ? deferSavingText(saving.data, half, t, num)
    : t("settings.defer.unavailable");
  return <div className="text-xs text-muted-foreground">{text}</div>;
}
