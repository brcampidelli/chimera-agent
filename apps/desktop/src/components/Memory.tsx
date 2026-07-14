import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Search, Trash2, Plus } from "lucide-react";
import { addMemory, deleteMemory, getMemory, getMemoryLayers } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { useT, type TFunc } from "@/lib/i18n";
import type { MemoryLayers } from "@/lib/types";

const inputCls = "field h-9 w-full px-3 text-sm";
const num = (n: number): string => n.toLocaleString();

type LayerRow = MemoryLayers["layers"][number];

/** A compact stat tile (label + value), mirroring the Usage screen's tile. */
function Tile({ label, value, tone }: { label: string; value: string; tone?: "ok" | "bad" }) {
  const color = tone === "ok" ? "text-ok" : tone === "bad" ? "text-bad" : "text-foreground";
  return (
    <div className="surface flex flex-col gap-1 p-4">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className={`font-mono text-xl ${color}`}>{value}</span>
    </div>
  );
}

/** One kind's row: name, count · clean N · unverified M, and a proportional bar (guard max ≥ 1). */
function LayerBar({ row, max, t }: { row: LayerRow; max: number; t: TFunc }) {
  const pct = row.count > 0 ? Math.max((row.count / max) * 100, 3) : 0;
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-xs text-foreground">{row.kind}</span>
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
          {num(row.count)} · {t("memory.layers.clean")} {num(row.clean)} ·{" "}
          {t("memory.layers.unverified")} {num(row.tainted)}
        </span>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-chip bg-white/[0.05]">
        <div className="h-full rounded-chip bg-accent-grad" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** The layers + provenance overview above the fact list, driven by /api/memory/layers. */
function LayersPanel({ t }: { t: TFunc }) {
  const q = useQuery({ queryKey: ["memory-layers"], queryFn: getMemoryLayers });
  const data = q.data;

  return (
    <Panel title={t("memory.layers.title")}>
      {q.isLoading || !data ? (
        <Spinner />
      ) : data.total === 0 ? (
        <EmptyState text={t("memory.layers.empty")} />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-3">
            <Tile label={t("memory.layers.total")} value={num(data.total)} />
            <Tile label={t("memory.layers.clean")} value={num(data.clean)} tone="ok" />
            <Tile label={t("memory.layers.unverified")} value={num(data.tainted)} tone="bad" />
          </div>
          <div className="px-4 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("memory.layers.byKind")}
          </div>
          {data.layers.map((row) => (
            <LayerBar key={row.kind} row={row} max={Math.max(...data.layers.map((l) => l.count), 1)} t={t} />
          ))}
          <div className="px-4 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t("memory.layers.bySource")}
          </div>
          {data.by_source.map((s) => (
            <div key={s.source || "—"} className="flex items-center justify-between gap-2 px-4 py-2">
              <span className="truncate font-mono text-xs text-foreground">{s.source || "—"}</span>
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{num(s.count)}</span>
            </div>
          ))}
          {!data.semantic_embeddings_enabled && (
            <div className="px-4 py-3 text-[11px] text-muted-foreground">
              {t("memory.layers.semanticOff")}
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

export function Memory() {
  const t = useT();
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");
  const [draft, setDraft] = useState("");
  const [persona, setPersona] = useState(false);
  const facts = useQuery({ queryKey: ["memory", term], queryFn: () => getMemory(term) });
  const add = useMutation({
    mutationFn: () => addMemory(draft.trim(), persona ? "persona" : "semantic"),
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["memory"] });
      qc.invalidateQueries({ queryKey: ["memory-layers"] });
    },
  });
  const remove = useMutation({
    mutationFn: deleteMemory,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory"] });
      qc.invalidateQueries({ queryKey: ["memory-layers"] });
    },
  });

  return (
    <Screen title={t("memory.title")} icon={<Brain className="h-5 w-5" />}>
      <LayersPanel t={t} />

      <Panel title={t("memory.addFact")}>
        <div className="flex items-center gap-2 px-4 py-3">
          <input
            className={inputCls}
            placeholder={t("memory.placeholder")}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && draft.trim() && add.mutate()}
          />
          <label className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
            <input type="checkbox" checked={persona} onChange={(e) => setPersona(e.target.checked)} />
            {t("memory.persona")}
          </label>
          <Button size="sm" disabled={!draft.trim() || add.isPending} onClick={() => add.mutate()}>
            <Plus className="h-4 w-4" /> {t("common.add")}
          </Button>
        </div>
      </Panel>

      <Panel
        title={t("memory.stored")}
        action={
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              setTerm(query.trim());
            }}
          >
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <input
              className="field h-7 w-40 px-2 text-xs"
              placeholder={t("common.search")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </form>
        }
      >
        {facts.isLoading ? (
          <Spinner />
        ) : !facts.data || facts.data.length === 0 ? (
          <EmptyState text={term ? t("memory.emptySearch") : t("memory.empty")} />
        ) : (
          facts.data.map((f) => (
            <div key={f.id} className="group flex items-start gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="text-sm">{f.content}</div>
                <div className="mt-1 flex items-center gap-1.5">
                  <Badge tone={f.kind === "persona" ? "accent" : "muted"}>{f.kind}</Badge>
                  {f.provenance === "tainted" && <Badge tone="warn">{t("memory.unverified")}</Badge>}
                </div>
              </div>
              <button
                className="opacity-0 transition group-hover:opacity-100"
                title={t("common.delete")}
                onClick={() => remove.mutate(f.id)}
              >
                <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-bad" />
              </button>
            </div>
          ))
        )}
      </Panel>
    </Screen>
  );
}
