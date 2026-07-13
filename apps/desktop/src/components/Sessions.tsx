import { Plus, Trash2, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SessionMeta } from "@/lib/types";

interface Props {
  sessions: SessionMeta[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

export function Sessions({ sessions, currentId, onSelect, onNew, onDelete }: Props) {
  return (
    <div className="flex h-full w-64 shrink-0 flex-col border-r border-white/5 bg-card/40">
      <div className="flex items-center gap-2 px-4 py-3.5">
        <span className="text-lg drop-shadow-[0_0_10px_hsl(var(--accent)/0.6)]" aria-hidden>
          🔺
        </span>
        <span className="font-semibold tracking-tight">Chimera</span>
      </div>
      <div className="px-3 pb-3">
        <Button size="sm" className="w-full" onClick={onNew}>
          <Plus className="h-4 w-4" /> New chat
        </Button>
      </div>
      <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 py-1">
        {sessions.length === 0 && (
          <p className="px-2 py-4 text-sm text-muted-foreground">No conversations yet.</p>
        )}
        {sessions.map((s) => (
          <div
            key={s.id}
            className={cn(
              "group flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-sm transition",
              s.id === currentId
                ? "bg-accent/12 text-foreground shadow-[inset_0_0_0_1px_hsl(var(--accent)/0.2)]"
                : "hover:bg-white/5",
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
    </div>
  );
}
