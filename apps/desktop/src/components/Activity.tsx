import type { CSSProperties } from "react";
import { Check, X, Wrench, Cpu, Brain, CircleDollarSign } from "lucide-react";
import { Fusion } from "@/components/Fusion";
import { MachinePanel } from "@/components/MachinePanel";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useAgent } from "@/lib/agent-context";

export type Status = "idle" | "thinking" | "streaming" | "done";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-hairline px-4 py-3">
      <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}

/** What the agent is doing, read from the shared state rather than handed down.
 *
 *  It used to take props from App, which meant only the chat could fill it. Reading the context lets
 *  the merged conversation feed the same panel — and stops App from being the one place that knows
 *  what an agent turn looks like. */
export function Activity() {
  const t = useT();
  const { status, tools, report } = useAgent();
  const cost =
    report == null
      ? null
      : report.usd == null
        ? t("activity.costUnavailable")
        : `~ $${report.usd.toFixed(4)}`;
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col overflow-y-auto border-l border-hairline bg-card/40">
      <div className="flex items-center gap-2 px-4 py-3.5">
        {/* Breathes only while something is happening. The glow is a static box-shadow and the
            pulse animates opacity — animating the shadow itself would repaint a large blurred
            area every frame for no visual gain. */}
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            status === "idle" ? "bg-muted-foreground" : "status-dot bg-accent shadow-status-dot",
          )}
        />
        <span className="text-sm font-medium">{t(`activity.${status}`)}</span>
      </div>

      <Section title={t("activity.tools")}>
        {tools.length === 0 ? (
          <div className="text-sm text-muted-foreground">{t("activity.noTools")}</div>
        ) : (
          <ul className="space-y-1.5">
            {tools.map((t, i) => (
              // The only per-event animation in the app. Each row rises 4px into place 40ms after
              // the one before it, so a burst of tool calls reads as the agent working through them
              // rather than as a list appearing all at once.
              <li
                key={i}
                className="event-enter flex items-center gap-2 text-sm"
                style={{ "--i": i } as CSSProperties}
              >
                {t.ok ? (
                  <Check className="h-3.5 w-3.5 text-ok" />
                ) : (
                  <X className="h-3.5 w-3.5 text-bad" />
                )}
                <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-mono text-sm">{t.name}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title={t("activity.tokens")}>
        <div className="flex items-center gap-2 text-sm">
          <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
          {report ? (
            <span className="font-mono">
              in {report.prompt_tokens} · out {report.completion_tokens}
              {(report.cache_read_tokens ?? 0) > 0 && ` · cache ${report.cache_read_tokens}`}
            </span>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </div>
        <div className="mt-1.5 flex items-center gap-2 text-sm">
          <CircleDollarSign className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="font-mono">{cost ?? "—"}</span>
          {report && report.usd != null && (
            <span className="text-xs text-muted-foreground">{t("activity.exclCache")}</span>
          )}
        </div>
      </Section>

      <Section title={t("activity.memory")}>
        <div className="flex items-center gap-2 text-sm">
          <Brain className="h-3.5 w-3.5 text-muted-foreground" />
          {/* An absent count is NOT zero: a surface that does not report recall would otherwise
              render "0 facts recalled", which is a measurement nobody took. */}
          {report && report.memory_facts_used != null ? (
            <span>
              {t("activity.factsRecalled", { n: report.memory_facts_used })}
              {report.memory_layer && (
                <span className="text-muted-foreground"> ({report.memory_layer})</span>
              )}
            </span>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </div>
      </Section>

      {/* The routing breakdown for this same turn. Renders nothing unless the turn used fusion or
          the cascade, so it never leaves an empty box behind. */}
      <Fusion report={report} />

      {/* What the machine is spending, beside what the agent is doing — the two questions someone
          watching a long run actually alternates between. Every reading is nullable and an absent
          one says so; see MachinePanel for why 0% would be the wrong answer. */}
      <Section title={t("machine.title")}>
        <MachinePanel />
      </Section>
    </aside>
  );
}
