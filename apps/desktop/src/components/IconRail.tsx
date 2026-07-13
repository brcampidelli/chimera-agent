import type { ComponentType } from "react";
import {
  MessageSquare,
  Brain,
  Sparkles,
  Clock,
  KanbanSquare,
  Settings as SettingsIcon,
  Moon,
  Sun,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type View = "chat" | "memory" | "skills" | "cron" | "tasks" | "settings";

const NAV: { view: View; label: string; icon: ComponentType<{ className?: string }> }[] = [
  { view: "chat", label: "Chat", icon: MessageSquare },
  { view: "memory", label: "Memory", icon: Brain },
  { view: "skills", label: "Skills", icon: Sparkles },
  { view: "cron", label: "Schedule", icon: Clock },
  { view: "tasks", label: "Tasks", icon: KanbanSquare },
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
  return (
    <div className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-white/5 bg-card/40 py-3">
      <div className="mb-2 select-none text-xl drop-shadow-[0_0_10px_hsl(var(--accent)/0.6)]" aria-hidden>
        🔺
      </div>
      <nav className="flex flex-1 flex-col items-center gap-1.5">
        {NAV.map((n) => (
          <RailButton
            key={n.view}
            active={view === n.view}
            label={n.label}
            icon={n.icon}
            onClick={() => onSelect(n.view)}
          />
        ))}
      </nav>
      <div className="flex flex-col items-center gap-1.5">
        <RailButton
          active={false}
          label={dark ? "Light theme" : "Dark theme"}
          icon={dark ? Sun : Moon}
          onClick={onToggleTheme}
        />
        <RailButton
          active={view === "settings"}
          label="Settings"
          icon={SettingsIcon}
          onClick={() => onSelect("settings")}
        />
      </div>
    </div>
  );
}
