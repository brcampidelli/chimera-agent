import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Screen({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 px-6 py-7">
        <div className="flex items-center gap-2.5 text-accent">
          {icon}
          <h1 className="text-lg font-semibold text-foreground">{title}</h1>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Panel({ title, action, children }: { title?: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="surface overflow-hidden">
      {title && (
        <div className="flex items-center justify-between border-b border-white/5 px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </div>
      )}
      <div className="divide-y divide-white/[0.04]">{children}</div>
    </section>
  );
}

type Tone = "muted" | "ok" | "warn" | "bad" | "accent";
const tones: Record<Tone, string> = {
  muted: "bg-white/[0.05] text-muted-foreground ring-1 ring-white/5",
  ok: "bg-ok/15 text-ok ring-1 ring-ok/20",
  warn: "bg-[hsl(38_92%_50%/0.15)] text-[hsl(38_92%_62%)] ring-1 ring-[hsl(38_92%_50%/0.25)]",
  bad: "bg-bad/15 text-bad ring-1 ring-bad/25",
  accent: "bg-accent/15 text-accent ring-1 ring-accent/25",
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
