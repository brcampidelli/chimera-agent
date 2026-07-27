import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KanbanSquare, ShieldAlert } from "lucide-react";
import { approveProject, denyProject, getKanban, getProject, getProjects } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { ProjectState, TaskCard } from "@/lib/types";

const COLUMN_ORDER = ["backlog", "doing", "review", "blocked", "done"];

function statusTone(s: string): "ok" | "accent" | "warn" | "bad" | "muted" {
  if (s === "done") return "ok";
  if (s === "running") return "accent";
  if (s === "awaiting_approval") return "warn";
  if (s === "escalated") return "bad";
  return "muted";
}

function ProjectRow({
  p,
  onChange,
  selected,
  onSelect,
}: {
  p: ProjectState;
  onChange: () => void;
  selected: boolean;
  onSelect: () => void;
}) {
  const t = useT();
  const approve = useMutation({
    mutationFn: (card?: string) => approveProject(p.id, card),
    onSuccess: onChange,
  });
  const deny = useMutation({ mutationFn: (card: string) => denyProject(p.id, card), onSuccess: onChange });
  const awaiting = p.status === "awaiting_approval";

  return (
    <div className={cn("px-4 py-3", selected && "bg-surface-2")}>
      <div className="flex items-center gap-2">
        {/* Selecting a project filters the board below to its own cards — which is what
            `/api/projects/{id}` returns and what nothing was calling. */}
        <button
          type="button"
          onClick={onSelect}
          aria-pressed={selected}
          className={cn("truncate font-mono text-sm hover:text-accent", focusRing)}
        >
          {p.id}
        </button>
        <Badge tone={statusTone(p.status)}>{p.status.replace("_", " ")}</Badge>
        <span className="text-xs text-muted-foreground">
          {t("tasks.iter", { a: p.iterations, b: p.max_iterations })}
        </span>
      </div>
      {p.note && <div className="mt-1 text-xs text-muted-foreground">{p.note}</div>}
      {awaiting && (
        <div className="mt-2 flex items-center gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 shadow-inset">
          <ShieldAlert className="h-4 w-4 text-warn-foreground" />
          <span className="flex-1 text-xs">
            {p.pending_card_id
              ? t("tasks.awaitingStep", { card: p.pending_card_id })
              : t("tasks.awaitingPlan")}
          </span>
          <Button size="sm" onClick={() => approve.mutate(p.pending_card_id ?? undefined)}>
            {p.pending_card_id ? t("tasks.approveStep") : t("tasks.approvePlan")}
          </Button>
          {p.pending_card_id && (
            <Button size="sm" variant="outline" onClick={() => deny.mutate(p.pending_card_id!)}>
              {t("tasks.deny")}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function Board({ columns }: { columns: Record<string, TaskCard[]> }) {
  const t = useT();
  const cols = COLUMN_ORDER.filter((c) => (columns[c]?.length ?? 0) > 0);
  if (cols.length === 0) return <EmptyState text={t("tasks.boardEmpty")} />;
  return (
    <div className="flex gap-3 overflow-x-auto p-3">
      {cols.map((col) => (
        <div key={col} className="w-56 shrink-0">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {col} · {columns[col].length}
          </div>
          <div className="space-y-2">
            {columns[col].map((c) => (
              <div
                key={c.id}
                className={`rounded-lg border border-hairline bg-card px-3 py-2.5 shadow-elev transition hover:brightness-105 ${
                  c.risk === "high" ? "ring-1 ring-bad/30" : ""
                }`}
              >
                <div className="text-sm">{c.title}</div>
                {c.risk === "high" && (
                  <div className="mt-1.5">
                    <Badge tone="bad">{t("tasks.highRisk")}</Badge>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function Tasks({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const qc = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const kanban = useQuery({ queryKey: ["kanban"], queryFn: getKanban });
  // Which project's board to show. `null` is the global board — every card from every project,
  // which is the right default and a poor way to follow one piece of work.
  const [selected, setSelected] = useState<string | null>(null);
  const project = useQuery({
    queryKey: ["project", selected],
    queryFn: () => getProject(selected!),
    enabled: selected !== null,
  });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["projects"] });
    qc.invalidateQueries({ queryKey: ["kanban"] });
  };

  return (
    <Screen title={t("tasks.title")} icon={<KanbanSquare className="h-5 w-5" />} embedded={embedded}>
      <Panel title={t("tasks.projects")}>
        {projects.isError ? (
          <ErrorState error={projects.error} onRetry={() => projects.refetch()} />
        ) : projects.isLoading ? (
          <Spinner />
        ) : !projects.data || projects.data.length === 0 ? (
          <EmptyState text={t("tasks.projectsEmpty")} />
        ) : (
          projects.data.map((p) => (
            <ProjectRow
              key={p.id}
              p={p}
              onChange={refresh}
              selected={selected === p.id}
              // Clicking the selected project clears the filter, so there is always a way back to
              // the whole board without hunting for a "show all" control.
              onSelect={() => setSelected((cur: string | null) => (cur === p.id ? null : p.id))}
            />
          ))
        )}
      </Panel>

      <Panel
        title={
          selected
            ? `${t("tasks.board")} · ${selected}`
            : t("tasks.board")
        }
      >
        {kanban.isError ? (
          <ErrorState error={kanban.error} onRetry={() => kanban.refetch()} />
        ) : kanban.isLoading || (selected && project.isLoading) ? (
          <Spinner />
        ) : (
          <Board columns={(selected ? project.data?.columns : kanban.data) ?? {}} />
        )}
      </Panel>
    </Screen>
  );
}
