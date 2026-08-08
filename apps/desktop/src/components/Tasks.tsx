import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KanbanSquare, Play, Plus, ShieldAlert, Square, Trash2 } from "lucide-react";
import {
  addKanbanCard,
  approveProject,
  denyProject,
  getKanban,
  getProject,
  getProjects,
  moveKanbanCard,
  removeKanbanCard,
  startProject,
  stepProject,
  streamKanbanRun,
} from "@/lib/api";
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
  onStep,
  stepping,
}: {
  p: ProjectState;
  onChange: () => void;
  selected: boolean;
  onSelect: () => void;
  onStep: () => void;
  stepping: boolean;
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
        <span className="flex-1 text-xs text-muted-foreground">
          {t("tasks.iter", { a: p.iterations, b: p.max_iterations })}
        </span>
        {/* One iteration per press, and the label says so rather than promising completion. A
            project is finished when its SPEC says so, which can take several — and a button that
            implied otherwise would be making a claim only the spec gets to make.
            Absent once done: `done` is terminal, and offering to advance it invites someone to
            restart work they already accepted. */}
        {p.status !== "done" && !awaiting && (
          <Button size="sm" variant="ghost" disabled={stepping} onClick={onStep}>
            <Play className="h-3.5 w-3.5" /> {t("tasks.step")}
          </Button>
        )}
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

/** Add a card, and say who works it.
 *
 * The lane is the interesting field and it is a free text box rather than a list of your agents:
 * `lane` has always been a plain string, an agent that does not exist yet is a perfectly good thing
 * to file work against (the card waits in the backlog until it does), and a dropdown would quietly
 * make "the agents you have right now" the limit of what you can plan for.
 */
function AddCard({ onAdd }: { onAdd: (card: { title: string; lane: string }) => void }) {
  const t = useT();
  const [title, setTitle] = useState("");
  const [lane, setLane] = useState("solve");

  return (
    <form
      className="flex flex-wrap items-center gap-2 px-3 pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        const value = title.trim();
        if (!value) return;
        onAdd({ title: value, lane: lane.trim() || "solve" });
        setTitle("");
      }}
    >
      <input
        className="field h-7 min-w-48 flex-1 px-2 text-xs"
        placeholder={t("tasks.newCard")}
        aria-label={t("tasks.newCard")}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <input
        className="field h-7 w-32 px-2 font-mono text-xs"
        placeholder="solve"
        aria-label={t("tasks.lane")}
        value={lane}
        onChange={(e) => setLane(e.target.value)}
      />
      <Button size="sm" type="submit" disabled={!title.trim()}>
        <Plus className="h-3.5 w-3.5" /> {t("tasks.add")}
      </Button>
    </form>
  );
}

function Board({
  columns,
  onMove,
  onRemove,
}: {
  columns: Record<string, TaskCard[]>;
  onMove: (id: string, column: string) => void;
  onRemove: (id: string) => void;
}) {
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
                className={`group/card rounded-lg border border-hairline bg-card px-3 py-2.5 shadow-elev transition hover:brightness-105 ${
                  c.risk === "high" ? "ring-1 ring-bad/30" : ""
                }`}
              >
                <div className="text-sm">{c.title}</div>
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {/* Which agent picks this up is the question a board is asked, so it is on the
                      card rather than behind it. */}
                  <Badge tone="muted">{c.lane}</Badge>
                  {c.verify && <Badge tone="accent">{t("tasks.verified")}</Badge>}
                  {c.risk === "high" && <Badge tone="bad">{t("tasks.highRisk")}</Badge>}
                </div>
                <div className="mt-1.5 flex items-center gap-1 opacity-0 focus-within:opacity-100 group-hover/card:opacity-100">
                  <select
                    className="field h-6 flex-1 px-1 text-xs"
                    aria-label={t("tasks.moveCard", { title: c.title })}
                    value={c.column}
                    onChange={(e) => onMove(c.id, e.target.value)}
                  >
                    {COLUMN_ORDER.map((target) => (
                      <option key={target} value={target}>
                        {target}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    aria-label={t("tasks.removeCard", { title: c.title })}
                    className="px-1 text-muted-foreground hover:text-bad"
                    onClick={() => onRemove(c.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Start a project from a spec.
 *
 * The field is a PATH, not spec text. The spec is the acceptance authority — the only thing that
 * decides whether the project is done — so it belongs in the repository, versioned and reviewable.
 * Writing one is a job for the coding conversation; pointing a project at it is this.
 */
function StartProject({ onStart }: { onStart: (spec: string) => void }) {
  const t = useT();
  const [spec, setSpec] = useState("");
  return (
    <form
      className="flex flex-wrap items-center gap-2 px-3 pb-2"
      onSubmit={(e) => {
        e.preventDefault();
        const value = spec.trim();
        if (!value) return;
        onStart(value);
        setSpec("");
      }}
    >
      <input
        className="field h-7 min-w-48 flex-1 px-2 font-mono text-xs"
        placeholder={t("tasks.specPath")}
        aria-label={t("tasks.specPath")}
        value={spec}
        onChange={(e) => setSpec(e.target.value)}
      />
      <Button size="sm" type="submit" disabled={!spec.trim()}>
        <Plus className="h-3.5 w-3.5" /> {t("tasks.startProject")}
      </Button>
    </form>
  );
}

/** The board's live dispatch: start it, stop it, and say what it is doing while it runs.
 *
 * A dispatch calls models for as long as the backlog has cards, so the button has to be a Stop as
 * well as a Play — and the board is refetched as each card lands rather than once at the end, since
 * watching a column empty is the whole reason someone presses it and stays.
 */
function useDispatch(onCard: () => void) {
  const t = useT();
  const [running, setRunning] = useState(false);
  const [line, setLine] = useState("");
  const abort = useRef<AbortController | null>(null);

  const toggle = () => {
    if (running) {
      abort.current?.abort();
      abort.current = null;
      setRunning(false);
      setLine("");
      return;
    }
    const controller = new AbortController();
    abort.current = controller;
    setRunning(true);
    setLine(t("tasks.dispatching"));
    void streamKanbanRun(
      {},
      {
        onCard: (outcome) => {
          setLine(`${outcome.card_id} → ${outcome.moved_to}`);
          onCard();
        },
        onDone: (summary) => {
          setRunning(false);
          // The count of cards actually WORKED, which is not the count that was queued: a card
          // whose lane has no runner waits rather than failing, and saying "0 worked" is how
          // someone learns their agent is not registered.
          setLine(summary.error ?? t("tasks.dispatched", { n: summary.worked }));
          onCard();
        },
        onError: (message) => {
          setRunning(false);
          setLine(message);
        },
      },
      controller.signal,
    );
  };

  return { running, line, toggle };
}

export function Tasks({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const qc = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: getProjects });
  const refetchBoard = () => {
    void qc.invalidateQueries({ queryKey: ["kanban"] });
  };
  const add = useMutation({ mutationFn: addKanbanCard, onSuccess: refetchBoard });
  const move = useMutation({
    mutationFn: ({ id, column }: { id: string; column: string }) => moveKanbanCard(id, column),
    onSuccess: refetchBoard,
  });
  const remove = useMutation({ mutationFn: removeKanbanCard, onSuccess: refetchBoard });
  const refetchProjects = () => {
    void qc.invalidateQueries({ queryKey: ["projects"] });
    refetchBoard();
  };
  const start = useMutation({
    mutationFn: (spec: string) => startProject({ spec }),
    onSuccess: refetchProjects,
  });
  // One call per iteration, and the button says so. A "run to completion" button over an endpoint
  // that cannot be interrupted would be a promise this screen could not keep.
  const step = useMutation({ mutationFn: stepProject, onSuccess: refetchProjects });
  const dispatch = useDispatch(refetchBoard);
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
          <>
            <StartProject onStart={(spec) => start.mutate(spec)} />
            <EmptyState text={t("tasks.projectsEmpty")} />
          </>
        ) : (
          projects.data.map((p) => (
            <ProjectRow
              key={p.id}
              p={p}
              onChange={refresh}
              onStep={() => step.mutate(p.id)}
              stepping={step.isPending}
              selected={selected === p.id}
              // Clicking the selected project clears the filter, so there is always a way back to
              // the whole board without hunting for a "show all" control.
              onSelect={() => setSelected((cur: string | null) => (cur === p.id ? null : p.id))}
            />
          ))
        )}
        {projects.data && projects.data.length > 0 && (
          <StartProject onStart={(spec) => start.mutate(spec)} />
        )}
      </Panel>

      <Panel
        title={
          selected
            ? `${t("tasks.board")} · ${selected}`
            : t("tasks.board")
        }
        action={
          // Only on the whole board: a project's cards are dispatched by the project loop, which
          // has its own approval gate. A Run button here would step around it.
          selected ? undefined : (
            <div className="flex items-center gap-2">
              {dispatch.line && (
                <span className="max-w-56 truncate text-xs text-muted-foreground">
                  {dispatch.line}
                </span>
              )}
              <Button size="sm" variant={dispatch.running ? "outline" : "ghost"} onClick={dispatch.toggle}>
                {dispatch.running ? (
                  <>
                    <Square className="h-3.5 w-3.5" /> {t("common.stop")}
                  </>
                ) : (
                  <>
                    <Play className="h-3.5 w-3.5" /> {t("tasks.run")}
                  </>
                )}
              </Button>
            </div>
          )
        }
      >
        {kanban.isError ? (
          <ErrorState error={kanban.error} onRetry={() => kanban.refetch()} />
        ) : kanban.isLoading || (selected && project.isLoading) ? (
          <Spinner />
        ) : (
          <>
            {/* Cards are added to the board, never to a project: a project's cards come from its
                spec, and one typed in by hand would be work its drift check does not know about. */}
            {!selected && <AddCard onAdd={(card) => add.mutate(card)} />}
            <Board
              columns={(selected ? project.data?.columns : kanban.data) ?? {}}
              onMove={(id, column) => move.mutate({ id, column })}
              onRemove={(id) => remove.mutate(id)}
            />
          </>
        )}
      </Panel>
    </Screen>
  );
}
