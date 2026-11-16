import { useQuery } from "@tanstack/react-query";

import { getRouting } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { TFunc } from "@/lib/i18n";
import type { RoutingRow } from "@/lib/types";

/** The model of a row as one line: a list role is its slugs, a role picked at use says so. */
export function routingModelText(row: RoutingRow, t: TFunc): string {
  const named = (m: { model: string; local_file?: boolean }) =>
    m.local_file ? `${m.model} (${t("settings.routing.localFile")})` : m.model;
  if (row.kind === "list") return row.members.length ? row.members.map(named).join(", ") : "—";
  return row.model ? named(row) : t("settings.routing.atUse");
}

function originText(origin: string, t: TFunc): string {
  if (origin === "env") return t("settings.routing.origin.env");
  if (origin === "config") return t("settings.routing.origin.config");
  return t("settings.routing.origin.default");
}

/** The i18n key that says a `via_kind` in the interface language; null when the table has no wording for
 *  it, and the cell then shows the technical text itself. The ladder's four ways of arriving at a rung
 *  read the same to a person, so they share one phrase; the exact sentence stays in the cell's title. */
const VIA_KEYS: Readonly<Record<string, string>> = {
  setting: "settings.routing.via.setting",
  preset: "settings.routing.via.preset",
  override: "settings.routing.via.override",
  replaced: "settings.routing.via.replaced",
  empty: "settings.routing.via.empty",
  ladder: "settings.routing.via.ladder",
  ladder_top: "settings.routing.via.ladder",
  escalation_measured: "settings.routing.via.ladder",
  fusion_ladder: "settings.routing.via.ladder",
  backend_default: "settings.routing.via.default",
  product_default: "settings.routing.via.default",
  profile: "settings.routing.via.profile",
  review_time: "settings.routing.via.review",
  unavailable: "settings.routing.via.unavailable",
};

/** What the Via cell shows: a role that follows another says which; otherwise the origin category in the
 *  interface language. The engineering sentence (`cost_mode auto preset`) is the tooltip, not the text. */
export function viaText(row: RoutingRow, t: TFunc): string {
  if (row.inherits) return t("settings.routing.follows", { role: row.inherits });
  const key = VIA_KEYS[row.via_kind];
  return key ? t(key) : row.via;
}

function effortText(row: RoutingRow, t: TFunc): string {
  if (!row.effort) return "—";
  return row.effort_sent === false
    ? `${row.effort} (${t("settings.routing.effortNotSent")})`
    : row.effort;
}

/**
 * Every role the product runs, with the model it uses and where that choice came from
 * (`GET /api/routing`). Read-only: the rows that change a model are above it. A role with no model of
 * its own says which role it follows, so "which model does the reviewer use?" has an answer here.
 */
export function RoutingTable() {
  const t = useT();
  const routing = useQuery({ queryKey: ["routing"], queryFn: () => getRouting(), retry: false });
  if (routing.isPending) return null;
  if (!routing.data) {
    return <div className="text-xs text-muted-foreground">{t("settings.routing.unavailable")}</div>;
  }
  if (routing.data.denied) {
    return (
      <div role="alert" className="text-xs text-warn-foreground">
        {t("settings.routing.denied", { reason: routing.data.denied })}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">{t("settings.routing.title")}</div>
      <div className="text-xs text-muted-foreground">{t("settings.routing.hint")}</div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs" aria-label={t("settings.routing.title")}>
          <thead className="bg-surface-2 text-muted-foreground">
            <tr>
              <th scope="col" className="px-2 py-1 font-medium">{t("settings.routing.role")}</th>
              <th scope="col" className="px-2 py-1 font-medium">{t("settings.routing.model")}</th>
              <th scope="col" className="px-2 py-1 font-medium">{t("settings.routing.effort")}</th>
              <th scope="col" className="px-2 py-1 font-medium">{t("settings.routing.origin")}</th>
              <th scope="col" className="px-2 py-1 font-medium">{t("settings.routing.via")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {routing.data.rows.map((row) => (
              <tr key={row.role}>
                <th scope="row" className="px-2 py-1 font-mono font-normal">{row.role}</th>
                <td className="px-2 py-1 font-mono break-all">{routingModelText(row, t)}</td>
                <td className="px-2 py-1">{effortText(row, t)}</td>
                <td className="px-2 py-1">{originText(row.origin, t)}</td>
                <td className="px-2 py-1 text-muted-foreground" title={row.via}>
                  {viaText(row, t)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
