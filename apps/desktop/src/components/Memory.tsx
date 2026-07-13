import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Search, Trash2, Plus } from "lucide-react";
import { addMemory, deleteMemory, getMemory } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";

const inputCls = "field h-9 w-full px-3 text-sm";

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
    },
  });
  const remove = useMutation({
    mutationFn: deleteMemory,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memory"] }),
  });

  return (
    <Screen title={t("memory.title")} icon={<Brain className="h-5 w-5" />}>
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
