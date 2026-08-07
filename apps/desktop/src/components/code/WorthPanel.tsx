import { useQuery } from "@tanstack/react-query";
import { Scale } from "lucide-react";
import { getWorth, type ProfileWorth } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";

/** A group's cost, or the honest absence of one.
 *
 *  `usd_total` is null the moment a single run in the group used a model whose price we do not
 *  know. Rendering that as $0.00 — or as nothing at all — would make the configuration that used a
 *  free or unpriced tier look like the cheap one, which is the exact conclusion this panel exists
 *  to let someone reach honestly. So it says how many of the runs were priced instead. */
function Cost({ group, t }: { group: ProfileWorth; t: TFunc }) {
  if (group.usd_total !== null) return <>${group.usd_total.toFixed(4)}</>;
  return (
    <span className="text-warn">
      {t("code.worth.costUnknown", { known: group.usd_known_runs, runs: group.runs })}
    </span>
  );
}

function Row({ group, t }: { group: ProfileWorth; t: TFunc }) {
  const label = group.profile
    ? t(`code.roles.profile.${group.profile}` as const)
    : t("code.worth.noProfile");
  return (
    <tr className="border-t border-hairline">
      <td className="py-1 pr-3">{label}</td>
      <td className="py-1 pr-3 text-right tabular-nums">{group.runs}</td>
      <td className="py-1 pr-3 text-right tabular-nums">
        {group.passed}
        {/* How many of those passes an executable command judged. Without it the number merged two
            different claims: a run approved by an exit code, and a run approved by a model reading
            the answer text — which never sees the diff, the transcript, or a file. Both passed; only
            one was verified. Shown as a fraction rather than a second column because the question is
            always "of these passes, how many", never "how many verifier passes exist". */}
        <span
          className={group.passed_by_verifier < group.passed ? "text-warn" : "text-ok"}
          title={t("code.worth.verifierNote")}
        >
          {" "}
          ({group.passed_by_verifier}
          {t("code.worth.withTests")})
        </span>
        {/* Beside the pass count, never folded into it: a "success" that changed no file is the
            empty-patch failure this project measured, and it must not inflate a pass rate. */}
        {group.unproductive > 0 ? (
          <span className="text-warn"> (−{group.unproductive})</span>
        ) : null}
      </td>
      <td className="py-1 pr-3 text-right tabular-nums">{group.attempts_total}</td>
      <td className="py-1 text-right tabular-nums">
        <Cost group={group} t={t} />
      </td>
    </tr>
  );
}

/** Was it worth it? — three facts per configuration, from the runs that really happened here.
 *
 *  The reason this is worth building at all: a benchmark published by whoever built the agent is
 *  always suspect, and one that ran on YOUR repo against YOUR verify command is a different kind of
 *  claim. That advantage survives exactly as long as the numbers stay honest, so what this panel
 *  REFUSES to do is the feature — it does not rank, does not fill in unknown costs, does not credit
 *  old runs to a profile they never used, and says out loud when n is too small to read.
 */
export function WorthPanel() {
  const t = useT();
  const worth = useQuery({ queryKey: ["worth"], queryFn: getWorth });

  if (!worth.data || worth.data.total_runs === 0) {
    return (
      <div className="space-y-1.5 border-t border-hairline p-3">
        <div className="flex items-center gap-2 text-accent">
          <Scale className="h-4 w-4" />
          <h2 className="text-sm font-semibold text-foreground">{t("code.worth.title")}</h2>
        </div>
        <p className="text-xs text-muted-foreground">{t("code.worth.empty")}</p>
      </div>
    );
  }

  const { profiles, any_readable, readable_n } = worth.data;
  return (
    <div className="space-y-2 border-t border-hairline p-3">
      <div className="flex items-center gap-2 text-accent">
        <Scale className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">{t("code.worth.title")}</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="text-muted-foreground">
            <tr>
              <th className="pb-1 pr-3 text-left font-normal">{t("code.worth.profile")}</th>
              <th className="pb-1 pr-3 text-right font-normal">{t("code.worth.runs")}</th>
              <th className="pb-1 pr-3 text-right font-normal">{t("code.worth.passed")}</th>
              <th className="pb-1 pr-3 text-right font-normal">{t("code.worth.attempts")}</th>
              <th className="pb-1 text-right font-normal">{t("code.worth.cost")}</th>
            </tr>
          </thead>
          <tbody>
            {profiles.map((group) => (
              <Row key={group.profile ?? "_none"} group={group} t={t} />
            ))}
          </tbody>
        </table>
      </div>
      {/* Shown when the data is thin, and NOT hidden once it is thick: these groups stay
          observational no matter how many rows accumulate. */}
      {!any_readable ? (
        <p className="text-xs text-warn">{t("code.worth.tooFew", { n: readable_n })}</p>
      ) : null}
      <p className="text-xs text-muted-foreground">{t("code.worth.notAnExperiment")}</p>
    </div>
  );
}
