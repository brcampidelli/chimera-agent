import type { CSSProperties } from "react";
import { Check, X, Wrench, Cpu, Brain, CircleDollarSign } from "lucide-react";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { TurnReport, ToolEvent } from "@/lib/types";

export type Status = "idle" | "thinking" | "streaming" | "done";

interface Props {
  status: Status;
  tools: ToolEvent[];
  report: TurnReport | null;
}

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

export function Activity({ status, tools, report }: Props) {
  const t = useT();
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
              {report.cache_read_tokens > 0 && ` · cache ${report.cache_read_tokens}`}
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
          {report ? (
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
    </aside>
  );
}
