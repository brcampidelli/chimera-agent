import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function Screen({
  title,
  icon,
  children,
  embedded = false,
}: {
  title: string;
  icon: ReactNode;
  children: ReactNode;
  /**
   * Rendered inside another screen's tab, which already supplies the heading, the scroll container
   * and the column width. Without this a nested screen brings a second `<h1>` and a second
   * scrollbar — two headings for one page is the kind of thing that reads fine and sounds wrong.
   */
  embedded?: boolean;
}) {
  if (embedded) return <div className="space-y-6">{children}</div>;
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
        <div className="flex items-center justify-between border-b border-hairline px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </div>
      )}
      <div className="divide-y divide-hairline">{children}</div>
    </section>
  );
}

type Tone = "muted" | "ok" | "warn" | "bad" | "accent";
const tones: Record<Tone, string> = {
  muted: "bg-surface-2 text-muted-foreground ring-1 ring-hairline",
  // Fill and ring take the plain token; the LABEL takes the -foreground pair. `warn` already did
  // this and the other two did not, which is the whole defect: measured on the light theme, `text-ok`
  // reads 3.06:1 as 11px text and `text-bad` 3.82:1, both under the 4.5 small text needs. The dark
  // theme passes on either, so the split only shows up on light — and only when read with a ruler.
  ok: "bg-ok/15 text-ok-foreground ring-1 ring-ok/20",
  warn: "bg-warn/15 text-warn-foreground ring-1 ring-warn/25",
  bad: "bg-bad/15 text-bad-foreground ring-1 ring-bad/25",
  accent: "bg-accent/15 text-accent-ink ring-1 ring-accent/25",
};

export function Badge({
  tone = "muted",
  title,
  children,
}: {
  tone?: Tone;
  /** The long form of what the badge names — a saved fact, a full model slug. A badge is a chip and
   *  the thing it stands for is often a sentence; without this the only way to read it is to go and
   *  find it somewhere else. */
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn("rounded-chip px-2 py-0.5 text-xs font-medium", tones[tone])}
      title={title}
    >
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
