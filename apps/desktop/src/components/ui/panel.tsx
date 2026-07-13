import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Screen({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 px-6 py-6">
        <div className="flex items-center gap-2">
          {icon}
          <h1 className="text-lg font-semibold">{title}</h1>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Panel({ title, action, children }: { title?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="border border-border bg-card">
      {title && (
        <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </div>
      )}
      <div className="divide-y divide-border">{children}</div>
    </section>
  );
}

type Tone = "muted" | "ok" | "warn" | "bad" | "accent";
const tones: Record<Tone, string> = {
  muted: "bg-muted text-muted-foreground",
  ok: "bg-ok/15 text-ok",
  warn: "bg-[hsl(38_92%_50%/0.15)] text-[hsl(38_92%_42%)]",
  bad: "bg-bad/15 text-bad",
  accent: "bg-accent/15 text-accent",
};

export function Badge({ tone = "muted", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={cn("rounded-chip px-2 py-0.5 text-[11px] font-medium", tones[tone])}>
      {children}
    </span>
  );
}

export function Spinner() {
  return (
    <div className="flex flex-1 items-center justify-center py-16 text-muted-foreground">
      <Loader2 className="h-5 w-5 animate-spin" />
    </div>
  );
}

export function EmptyState({ text }: { text: string }) {
  return <div className="px-4 py-6 text-sm text-muted-foreground">{text}</div>;
}
