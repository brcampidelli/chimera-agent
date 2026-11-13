import { useT } from "@/lib/i18n";
import type { MonitorFinding, RunMonitor } from "@/lib/orchestration-run";

/**
 * What the cross-agent monitor said about a run (`governance/aggregate_monitor.py`).
 *
 * A worker's own record cannot see a flow split across workers: one reads a page, another acts on
 * it, and neither alone did anything worth a question. The server reads every worker's record at the
 * end of a crew or hierarchy run and sends this section. Three states, kept apart on purpose:
 *
 *  - not evaluated — there was no record to read (governance off, or no worker kept one). It must
 *    never read as "nothing found": a clean result from a monitor that read nothing is a false
 *    reassurance;
 *  - evaluated, nothing found;
 *  - evaluated, something worth a look. It only asks for review; nothing was stopped, and the
 *    sentence says so rather than implying a block that did not happen.
 */
export function MonitorSection({ monitor }: { monitor: RunMonitor | null }) {
  const t = useT();
  if (!monitor) return null;
  if (!monitor.evaluated) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="run-monitor" data-state="not-evaluated">
        {monitor.reason === "governance_off"
          ? t("orch.monitor.notEvaluatedOff")
          : t("orch.monitor.notEvaluatedNoRecord")}
      </p>
    );
  }
  if (monitor.findings.length === 0) {
    return (
      <p className="text-xs text-muted-foreground" data-testid="run-monitor" data-state="clean">
        {t("orch.monitor.clean", { n: monitor.agents })}
      </p>
    );
  }
  return (
    <section
      className="rounded-card border border-warn/25 bg-warn/5 p-3 text-sm"
      data-testid="run-monitor"
      data-state="found"
    >
      <p className="text-warn-foreground">{t("orch.monitor.found", { n: monitor.agents })}</p>
      <ul className="mt-2 space-y-1">
        {monitor.findings.map((f, i) => (
          <li key={`${f.kind}-${i}`} className="text-xs text-muted-foreground" title={f.detail}>
            <FindingLabel finding={f} />
            {f.agents.length ? (
              <span> · {t("orch.monitor.workers", { names: f.agents.join(", ") })}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function FindingLabel({ finding }: { finding: MonitorFinding }) {
  const t = useT();
  // Keyed off the machine-readable kind, never the server's English `detail`, which stays in the
  // tooltip for whoever wants the raw line.
  if (finding.kind === "cross-agent-taint") return <span>{t("orch.monitor.kind.split")}</span>;
  if (finding.kind === "fan-out-volume") return <span>{t("orch.monitor.kind.volume")}</span>;
  return <span>{finding.detail || finding.kind}</span>;
}
