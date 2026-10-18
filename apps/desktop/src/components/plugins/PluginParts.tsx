import { Puzzle, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { PluginComponent, PluginDiff } from "@/lib/types";

/** The icon a plugin would show. None of them ships one we trust to draw, so every card gets the
 *  same mark: a picture taken from a stranger's package is one more thing to vouch for. */
export function PluginIcon() {
  return (
    <span
      aria-hidden
      className="flex h-9 w-9 shrink-0 items-center justify-center rounded-card bg-surface-2 text-muted-foreground ring-1 ring-hairline"
    >
      <Puzzle className="h-4 w-4" />
    </span>
  );
}

const STATUS_KEY: Record<string, string> = {
  pending: "plugins.status.pending",
  active: "plugins.status.active",
  inactive: "plugins.status.inactive",
  held: "plugins.status.held",
};

const STATUS_TONE: Record<string, "ok" | "warn" | "muted"> = {
  pending: "warn",
  active: "ok",
  inactive: "muted",
  held: "warn",
};

export function StatusBadge({ status, denied }: { status: string; denied?: boolean }) {
  const t = useT();
  if (denied) return <Badge tone="bad">{t("plugins.status.denied")}</Badge>;
  return <Badge tone={STATUS_TONE[status] ?? "muted"}>{t(STATUS_KEY[status] ?? "plugins.status.pending")}</Badge>;
}

/** What each component is, in one word the card can afford: reaches the agent, a server, inert. */
function ComponentNote({ c }: { c: PluginComponent }) {
  const t = useT();
  if (c.inert) return <span className="text-warn-foreground">{t("plugins.component.inert")}</span>;
  if (c.kind === "mcp_server") return <span>{t("plugins.component.server")}</span>;
  if (c.reaches_agent) return <span>{t("plugins.component.reaches")}</span>;
  return null;
}

/** Collapsed by default: a plugin can bring dozens of parts, and the capability line above already
 *  says what matters. `<details>` is keyboard-reachable and announced without any code here. */
export function ComponentList({ components }: { components: PluginComponent[] }) {
  const t = useT();
  if (components.length === 0) return null;
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        {t("plugins.components", { n: components.length })}
      </summary>
      <ul className="mt-1 space-y-0.5 pl-3">
        {components.map((c) => (
          <li key={`${c.kind}:${c.name}:${c.path}`} className="flex flex-wrap gap-x-2 text-muted-foreground">
            <span className="font-mono text-foreground">{c.name}</span>
            <span>{c.kind}</span>
            <ComponentNote c={c} />
          </li>
        ))}
      </ul>
    </details>
  );
}

/** The change an update brings, as data: files, components and why it is governed. Shown before the
 *  owner accepts it — the check — and on a held plugin until it is switched on again. */
export function DiffBlock({ diff, title }: { diff: PluginDiff; title: string }) {
  const t = useT();
  const added = diff.files_added ?? [];
  const removed = diff.files_removed ?? [];
  const changed = diff.files_changed ?? [];
  const parts = [
    ...(diff.components_added ?? []).map((c) => `+ ${c.kind} ${c.name}`),
    ...(diff.components_removed ?? []).map((c) => `− ${c.kind} ${c.name}`),
    ...(diff.components_changed ?? []).map((c) => `~ ${c.kind} ${c.name}`),
  ];
  return (
    <div className="flex flex-col gap-1 rounded-chip border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn-foreground">
      <div className="flex items-start gap-1.5">
        <TriangleAlert aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{title}</span>
      </div>
      <span className="text-foreground">
        {t("plugins.diff.files", { added: added.length, removed: removed.length, changed: changed.length })}
      </span>
      <ul className="font-mono text-foreground">
        {[...added.map((f) => `+ ${f}`), ...removed.map((f) => `− ${f}`), ...changed.map((f) => `~ ${f}`)]
          .slice(0, 30)
          .map((line) => (
            <li key={line}>{line}</li>
          ))}
        {parts.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <span>
        {diff.governed
          ? t("plugins.diff.governed", { reasons: (diff.reasons ?? []).join(", ") })
          : t("plugins.diff.prose")}
      </span>
    </div>
  );
}
