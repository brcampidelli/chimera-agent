import { Plus, Trash2, MessageSquare, Settings as SettingsIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SessionMeta } from "@/lib/types";

interface Props {
  sessions: SessionMeta[];
  currentId: string | null;
  settingsActive: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onOpenSettings: () => void;
}

export function Sessions({
  sessions,
  currentId,
  settingsActive,
  onSelect,
  onNew,
  onDelete,
  onOpenSettings,
}: Props) {
  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-border bg-card">
      <div className="flex items-center gap-2 px-3 py-3">
        <span className="text-lg" aria-hidden>
          🔺
        </span>
        <span className="font-semibold">Chimera</span>
      </div>
      <div className="px-3 pb-2">
        <Button size="sm" className="w-full" onClick={onNew}>
          <Plus className="h-4 w-4" /> New chat
        </Button>
      </div>
      <nav className="flex-1 overflow-y-auto px-2 py-1">
        {sessions.length === 0 && (
          <p className="px-2 py-4 text-sm text-muted-foreground">No conversations yet.</p>
        )}
        {sessions.map((s) => (
          <div
            key={s.id}
            className={cn(
              "group flex items-center gap-2 px-2 py-2 text-sm cursor-pointer",
              s.id === currentId ? "bg-muted" : "hover:bg-muted/60",
            )}
            onClick={() => onSelect(s.id)}
          >
            <MessageSquare className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="flex-1 truncate">{s.title}</span>
            <button
              className="opacity-0 transition group-hover:opacity-100"
              title="Delete"
              onClick={(e) => {
                e.stopPropagation();
                onDelete(s.id);
              }}
            >
              <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-bad" />
            </button>
          </div>
        ))}
      </nav>
      <button
        className={cn(
          "flex items-center gap-2 border-t border-border px-4 py-3 text-sm",
          settingsActive ? "bg-muted" : "hover:bg-muted/60",
        )}
        onClick={onOpenSettings}
      >
        <SettingsIcon className="h-4 w-4 text-muted-foreground" />
        Settings
      </button>
    </div>
  );
}
