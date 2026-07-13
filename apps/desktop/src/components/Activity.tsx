import { Check, X, Wrench, Cpu, Brain, CircleDollarSign } from "lucide-react";
import type { TurnReport, ToolEvent } from "@/lib/types";

export type Status = "idle" | "thinking" | "streaming" | "done";

interface Props {
  status: Status;
  tools: ToolEvent[];
  report: TurnReport | null;
}

const statusLabel: Record<Status, string> = {
  idle: "idle",
  thinking: "thinking…",
  streaming: "streaming…",
  done: "done",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-white/5 px-4 py-3">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </div>
      {children}
    </div>
  );
}

export function Activity({ status, tools, report }: Props) {
  const cost =
    report == null
      ? null
      : report.usd == null
        ? "unavailable"
        : `~ $${report.usd.toFixed(4)}`;
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col overflow-y-auto border-l border-white/5 bg-card/40">
      <div className="flex items-center gap-2 px-4 py-3.5">
        <span
          className={`h-2 w-2 rounded-full ${
            status === "idle"
              ? "bg-muted-foreground"
              : "animate-pulse bg-accent shadow-[0_0_10px_1px_hsl(var(--accent)/0.8)]"
          }`}
        />
        <span className="text-sm font-medium">{statusLabel[status]}</span>
      </div>

      <Section title="Tools">
        {tools.length === 0 ? (
          <div className="text-sm text-muted-foreground">no tools this turn</div>
        ) : (
          <ul className="space-y-1.5">
            {tools.map((t, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                {t.ok ? (
                  <Check className="h-3.5 w-3.5 text-ok" />
                ) : (
                  <X className="h-3.5 w-3.5 text-bad" />
                )}
                <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="font-mono text-[13px]">{t.name}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Tokens">
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
            <span className="text-[11px] text-muted-foreground">(excl. cache)</span>
          )}
        </div>
      </Section>

      <Section title="Memory">
        <div className="flex items-center gap-2 text-sm">
          <Brain className="h-3.5 w-3.5 text-muted-foreground" />
          {report ? (
            <span>
              {report.memory_facts_used} fact{report.memory_facts_used === 1 ? "" : "s"} recalled
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
