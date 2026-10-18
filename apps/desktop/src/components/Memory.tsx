import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Info, Search, Plus } from "lucide-react";
import { addMemory, deleteMemory, getMemory, getMemoryLayers } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { MemoryFactRow } from "@/components/MemoryFactRow";
import { MemoryTools } from "@/components/MemoryTools";
import { ErrorState } from "@/components/ui/async";
import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { useNum, useT, type TFunc } from "@/lib/i18n";
import { memoryKindLabel } from "@/lib/memoryKind";
import { memorySourceLabel } from "@/lib/memorySource";
import type { MemoryLayers } from "@/lib/types";

const inputCls = "field h-9 w-full px-3 text-sm";
const infoBtn = `rounded-chip p-0.5 text-muted-foreground hover:text-foreground ${focusRing}`;
const linkBtn = `rounded-chip text-accent-ink hover:underline ${focusRing}`;

type LayerRow = MemoryLayers["layers"][number];

/** A compact stat tile (label + value), mirroring the Usage screen's tile. */
function Tile({ label, value, tone }: { label: string; value: string; tone?: "ok" | "warn" }) {
  const color = tone === "ok" ? "text-ok-foreground" : tone === "warn" ? "text-warn-foreground" : "text-foreground";
  return (
    <div className="surface flex flex-col gap-1 p-4">
      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span className={`font-mono text-xl ${color}`}>{value}</span>
    </div>
  );
}

/** One kind's row: name, count · clean N · unverified M, and a proportional bar (guard max ≥ 1). */
function LayerBar({ row, max, t }: { row: LayerRow; max: number; t: TFunc }) {
  const num = useNum();
  const pct = row.count > 0 ? Math.max((row.count / max) * 100, 3) : 0;
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs font-medium text-foreground">{memoryKindLabel(t, row.kind)}</span>
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {num(row.count)} · {t("memory.layers.clean")} {num(row.clean)} ·{" "}
          {t("memory.layers.unverified")} {num(row.tainted)}
        </span>
      </div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-chip bg-surface-2">
        <div className="h-full rounded-chip bg-accent-grad" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** Where the facts came from, in plain words: what "Clean" and "Unverified" mean, said once. The
 *  two labels are the store's trust split (a source Chimera trusts × outside content), and on their
 *  own they read as jargon — "clean" of what? */
function TrustHint({ t }: { t: TFunc }) {
  return <p className="px-4 pb-3 text-xs text-muted-foreground">{t("memory.trust.hint")}</p>;
}

/** The layers + provenance overview, driven by /api/memory/layers. It sits BELOW the facts: the facts
 *  are what the owner reads and edits, and three big counters above them pushed the list off the
 *  first screen. */
function LayersPanel({ t }: { t: TFunc }) {
  const num = useNum();
  const q = useQuery({ queryKey: ["memory-layers"], queryFn: getMemoryLayers });
  const data = q.data;
  // A kind with nothing in it is a row that says nothing. The server still reports all four kinds
  // (an honest 0 on the wire, see `memory_layers.py`); the screen just does not spend a line on it.
  const kinds = (data?.layers ?? []).filter((row) => row.count > 0);
  const max = Math.max(...kinds.map((l) => l.count), 1);

  return (
    <Panel title={t("memory.layers.title")}>
      {q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.isLoading || !data ? (
        <Spinner />
      ) : data.total === 0 ? (
        <EmptyState text={t("memory.layers.empty")} />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-3">
            <Tile label={t("memory.layers.total")} value={num(data.total)} />
            <Tile label={t("memory.layers.clean")} value={num(data.clean)} tone="ok" />
            {/* Amber, not red: an unverified fact is one to review, not an error. Red here read as
                "something broke" on a screen where nothing had. */}
            <Tile label={t("memory.layers.unverified")} value={num(data.tainted)} tone="warn" />
          </div>
          <TrustHint t={t} />
          {kinds.length > 0 && (
            <div className="px-4 pb-1 pt-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {t("memory.layers.byKind")}
            </div>
          )}
          {kinds.map((row) => (
            <LayerBar key={row.kind} row={row} max={max} t={t} />
          ))}
          {(data.by_source ?? []).length > 0 && (
            <div className="px-4 pb-1 pt-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {t("memory.layers.bySource")}
            </div>
          )}
          {(data.by_source ?? []).map((s) => (
            <div key={s.source || "—"} className="flex items-center justify-between gap-2 px-4 py-2">
              {/* In words, not the store's id in monospace ("chimera" read as jargon). */}
              <span className="truncate text-xs text-foreground">{s.source ? memorySourceLabel(t, s.source) : "—"}</span>
              <span className="shrink-0 font-mono text-xs text-muted-foreground">{num(s.count)}</span>
            </div>
          ))}
          {!data.semantic_embeddings_enabled && (
            <div className="flex items-center gap-1.5 px-4 py-3 text-xs text-muted-foreground">
              {/* What it means for the person searching, said plainly; the engineering version
                  ("embeddings", "opt-in", "vector index") stays one hover or focus away. */}
              <span>{t("memory.layers.semanticOffPlain")}</span>
              <Tooltip label={t("memory.layers.semanticOff")} side="top">
                <button type="button" className={infoBtn} aria-label={t("memory.layers.technical")}>
                  <Info className="h-3.5 w-3.5" />
                </button>
              </Tooltip>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

/** One line over the list: how many facts there are and — when any are unverified — a way to see just
 *  those. "2 unverified" used to be a counter you could read and not act on; the review it asks for
 *  is a click away now. */
function FactsSummary({
  t,
  onlyUnverified,
  setOnlyUnverified,
}: {
  t: TFunc;
  onlyUnverified: boolean;
  setOnlyUnverified: (on: boolean) => void;
}) {
  const num = useNum();
  const q = useQuery({ queryKey: ["memory-layers"], queryFn: getMemoryLayers });
  const data = q.data;
  if (onlyUnverified) {
    return (
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-xs text-muted-foreground">
        <span>{t("memory.summary.filtered")}</span>
        <button type="button" className={linkBtn} onClick={() => setOnlyUnverified(false)}>
          {t("memory.summary.showAll")}
        </button>
      </div>
    );
  }
  if (!data || data.total === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-xs text-muted-foreground">
      <span>{t("memory.summary.total", { n: num(data.total) })}</span>
      {data.tainted > 0 && (
        <>
          <span aria-hidden>·</span>
          <button type="button" className={`rounded-chip font-medium text-warn-foreground hover:underline ${focusRing}`} onClick={() => setOnlyUnverified(true)}>
            {t("memory.summary.unverified", { n: num(data.tainted) })}
          </button>
          <Tooltip label={t("memory.trust.hint")} side="top">
            <button type="button" className={infoBtn} aria-label={t("memory.trust.what")}>
              <Info className="h-3.5 w-3.5" />
            </button>
          </Tooltip>
        </>
      )}
    </div>
  );
}

export function Memory({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");
  const [draft, setDraft] = useState("");
  const [persona, setPersona] = useState(false);
  const [onlyUnverified, setOnlyUnverified] = useState(false);
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

  // The filter is applied here, over what the route returned, rather than as a new query: the list
  // without a search term is the whole store, so "only unverified" over it is the complete set.
  const shown = onlyUnverified ? facts.data?.filter((f) => f.provenance === "tainted") : facts.data;

  return (
    <Screen title={t("memory.title")} icon={<Brain className="h-5 w-5" />} embedded={embedded}>
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
        <FactsSummary t={t} onlyUnverified={onlyUnverified} setOnlyUnverified={setOnlyUnverified} />
        {facts.isError ? (
          <ErrorState error={facts.error} onRetry={() => facts.refetch()} />
        ) : facts.isLoading ? (
          <Spinner />
        ) : !shown || shown.length === 0 ? (
          <EmptyState
            text={
              onlyUnverified && facts.data && facts.data.length > 0
                ? t("memory.filter.noneUnverified")
                : term
                  ? t("memory.emptySearch")
                  : t("memory.empty")
            }
          />
        ) : (
          shown.map((f) => <MemoryFactRow key={f.id} fact={f} onDelete={(id) => remove.mutate(id)} />)
        )}
      </Panel>

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

      <LayersPanel t={t} />

      <MemoryTools />
    </Screen>
  );
}
