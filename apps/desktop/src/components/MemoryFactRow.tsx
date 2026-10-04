import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, Trash2, X } from "lucide-react";
import { editMemory } from "@/lib/api";
import { Badge } from "@/components/ui/panel";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import type { MemoryItem } from "@/lib/types";

const iconBtn = `rounded-chip p-1 text-muted-foreground hover:text-foreground ${focusRing}`;

/** One stored fact: its text, its labels, and — on demand — an inline editor for the text.
 *
 * Before this a fact with one wrong word had to be deleted and typed again, which also threw away
 * what the store knew about it (when it was written, which project it belongs to). Editing keeps all
 * of that, and keeps the trust label: the note under the editor says so, because a person rewording
 * an "unverified" fact would reasonably expect their edit to vouch for it, and it does not.
 */
export function MemoryFactRow({ fact, onDelete }: { fact: MemoryItem; onDelete: (id: string) => void }) {
  const t = useT();
  const qc = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (content: string) => editMemory(fact.id, content),
    onSuccess: () => {
      setDraft(null);
      qc.invalidateQueries({ queryKey: ["memory"] });
    },
  });
  const editing = draft !== null;
  const canSave = editing && draft.trim() !== "" && draft.trim() !== fact.content.trim() && !save.isPending;

  return (
    <div className="group flex items-start gap-3 px-4 py-3">
      <div className="min-w-0 flex-1">
        {editing ? (
          <form
            className="flex items-start gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              if (canSave) save.mutate(draft.trim());
            }}
          >
            {/* A textarea, not an input: an <input> strips line breaks from its value, so opening a
                multi-line fact (a merged summary, a note remembered from a long message) flattened
                it, enabled Save on an untouched fact, and saving rewrote its lines. Enter still
                saves; Shift+Enter is a new line, as in the chat box. */}
            <textarea
              className="field w-full resize-y px-2 py-1.5 text-sm"
              aria-label={t("common.edit")}
              rows={Math.min(8, Math.max(1, draft.split("\n").length))}
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setDraft(null);
                else if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  if (canSave) save.mutate(draft.trim());
                }
              }}
            />
            <button type="submit" className={iconBtn} aria-label={t("common.save")} disabled={!canSave}>
              <Check className="h-3.5 w-3.5" />
            </button>
            <button type="button" className={iconBtn} aria-label={t("common.cancel")} onClick={() => setDraft(null)}>
              <X className="h-3.5 w-3.5" />
            </button>
          </form>
        ) : (
          <div className="whitespace-pre-line text-sm">{fact.content}</div>
        )}
        {editing && fact.provenance === "tainted" && (
          <div className="mt-1 text-xs text-muted-foreground">{t("memory.editKeepsLabel")}</div>
        )}
        <div className="mt-1 flex items-center gap-1.5">
          <Badge tone={fact.kind === "persona" ? "accent" : "muted"}>{fact.kind}</Badge>
          {fact.provenance === "tainted" && <Badge tone="warn">{t("memory.unverified")}</Badge>}
          {/* Only the exception is labelled. What the agent learns is now saved into the folder it
              was learned in, so "this one applies everywhere" is the fact worth pointing at — a badge
              on every row would say the ordinary case out loud and bury the one that differs. */}
          {/* Falsy, not `=== null`: a store written before the field existed returns the fact with
              no `project` key at all, and the backend folds null and "" into the same answer. */}
          {!fact.project && (
            <Badge tone="accent" title={t("memory.everywhereHint")}>
              {t("memory.everywhere")}
            </Badge>
          )}
          {fact.source !== "chimera" && <Badge tone="muted">{fact.source}</Badge>}
        </div>
      </div>
      {!editing && (
        <div className="flex shrink-0 items-center gap-1">
          <button
            className={iconBtn}
            aria-label={t("common.edit")}
            title={t("common.edit")}
            onClick={() => setDraft(fact.content)}
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            className={`${iconBtn} hover:text-bad`}
            aria-label={t("common.delete")}
            title={t("common.delete")}
            onClick={() => onDelete(fact.id)}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
