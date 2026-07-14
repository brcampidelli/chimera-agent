import type { ComponentType } from "react";
import {
  MessageSquare,
  Brain,
  Sparkles,
  Clock,
  KanbanSquare,
  Network,
  BarChart3,
  ListChecks,
  Wrench,
  Shield,
  Gauge,
  Settings as SettingsIcon,
  Moon,
  Sun,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { BrandMark } from "@/components/BrandMark";
import { useT } from "@/lib/i18n";

export type View =
  | "chat"
  | "memory"
  | "skills"
  | "cron"
  | "tasks"
  | "fusion"
  | "usage"
  | "runs"
  | "tools"
  | "governance"
  | "maturity"
  | "settings";

const NAV: { view: View; labelKey: string; icon: ComponentType<{ className?: string }> }[] = [
  { view: "chat", labelKey: "nav.chat", icon: MessageSquare },
  { view: "memory", labelKey: "nav.memory", icon: Brain },
  { view: "skills", labelKey: "nav.skills", icon: Sparkles },
  { view: "cron", labelKey: "nav.schedule", icon: Clock },
  { view: "tasks", labelKey: "nav.tasks", icon: KanbanSquare },
  { view: "fusion", labelKey: "nav.fusion", icon: Network },
  { view: "usage", labelKey: "nav.usage", icon: BarChart3 },
  { view: "runs", labelKey: "nav.runs", icon: ListChecks },
  { view: "tools", labelKey: "nav.tools", icon: Wrench },
  { view: "governance", labelKey: "nav.governance", icon: Shield },
  { view: "maturity", labelKey: "nav.maturity", icon: Gauge },
];

function RailButton({
  active,
  label,
  onClick,
  icon: Icon,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  icon: ComponentType<{ className?: string }>;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className={cn(
        "relative flex h-11 w-11 items-center justify-center rounded-xl2 transition-all duration-150",
        active
          ? "bg-accent/15 text-accent shadow-[inset_0_0_0_1px_hsl(var(--accent)/0.3),0_0_16px_-4px_hsl(var(--accent)/0.6)]"
          : "text-muted-foreground hover:bg-white/5 hover:text-foreground",
      )}
    >
      <Icon className="h-5 w-5" />
    </button>
  );
}

export function IconRail({
  view,
  onSelect,
  dark,
  onToggleTheme,
}: {
  view: View;
  onSelect: (v: View) => void;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  const t = useT();
  return (
    <div className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-white/5 bg-card/40 py-3">
      <BrandMark className="mb-2 h-8 w-8" glow />
      <nav className="flex flex-1 flex-col items-center gap-1.5">
        {NAV.map((n) => (
          <RailButton
            key={n.view}
            active={view === n.view}
            label={t(n.labelKey)}
            icon={n.icon}
            onClick={() => onSelect(n.view)}
          />
        ))}
      </nav>
      <div className="flex flex-col items-center gap-1.5">
        <RailButton
          active={false}
          label={dark ? t("theme.light") : t("theme.dark")}
          icon={dark ? Sun : Moon}
          onClick={onToggleTheme}
        />
        <RailButton
          active={view === "settings"}
          label={t("nav.settings")}
          icon={SettingsIcon}
          onClick={() => onSelect("settings")}
        />
      </div>
    </div>
  );
}
