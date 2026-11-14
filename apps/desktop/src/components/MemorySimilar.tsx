import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteMemory, getSimilarMemory } from "@/lib/api";
import { Badge, Panel } from "@/components/ui/panel";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import type { MemoryItem } from "@/lib/types";

const removeBtn = `rounded-chip text-xs text-accent-ink hover:underline ${focusRing}`;

function when(item: MemoryItem): string {
  return item.created_at == null ? "" : new Date(item.created_at * 1000).toLocaleDateString();
}

/** Two facts that say nearly the same thing and were written on different days.
 *
 * Those are the pairs recall cannot arbitrate: an old claim and its correction are both still
 * stored, and either can be read into the next answer. The screen lists each pair with its dates
 * and lets the owner remove the one they do not want, through the ordinary delete route. It draws
 * nothing while loading, on error, or when there is no pair: a panel that says "nothing to review"
 * on every visit is one nobody reads on the day there is something.
 */
export function MemorySimilar() {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["memory-similar"], queryFn: () => getSimilarMemory() });
  const remove = useMutation({
    mutationFn: (id: string) => deleteMemory(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memory"] });
      qc.invalidateQueries({ queryKey: ["memory-layers"] });
      qc.invalidateQueries({ queryKey: ["memory-similar"] });
    },
  });
  const pairs = q.data?.pairs ?? [];
  if (pairs.length === 0) return null;

  return (
    <Panel title={t("memory.similar.title")}>
      <p className="px-4 pt-3 text-xs text-muted-foreground">{t("memory.similar.hint")}</p>
      {pairs.map((pair) => (
        <div
          key={`${pair.a.id}:${pair.b.id}`}
          role="group"
          aria-label={t("memory.similar.title")}
          className="grid grid-cols-1 gap-3 px-4 py-3 sm:grid-cols-2"
        >
          {[pair.a, pair.b].map((fact) => (
            <div key={fact.id} className="flex flex-col gap-1.5">
              <p className="break-words text-sm text-foreground">{fact.content}</p>
              <p className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                {when(fact) ? <span>{t("memory.similar.written", { date: when(fact) })}</span> : null}
                {fact.provenance === "tainted" ? <Badge tone="warn">{t("memory.unverified")}</Badge> : null}
              </p>
              <div>
                <button
                  type="button"
                  className={removeBtn}
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(fact.id)}
                >
                  {t("memory.similar.remove")}
                </button>
              </div>
            </div>
          ))}
        </div>
      ))}
    </Panel>
  );
}
