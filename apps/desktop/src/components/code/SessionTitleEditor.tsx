import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";

import { renameCodeSession, type CodeSessionMeta } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** The inline form that names a conversation from its sidebar row.
 *
 *  Starts from the owner's own name when there is one, and empty otherwise: pre-filling the
 *  automatic title would make "save without typing" freeze it as the owner's, and the automatic
 *  title is the one that can still get better (a caller's title, a task file's heading). Saving an
 *  empty field gives the conversation back its automatic name. Enter saves, Escape cancels, and
 *  the caller puts focus back on the row (:func:`focusSessionRow`) so the keyboard is not dropped
 *  onto the page when the form goes away. */
export function SessionTitleEditor({
  session,
  onClose,
}: {
  session: CodeSessionMeta;
  onClose: () => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [draft, setDraft] = useState(session.title_source === "owner" ? session.title : "");
  const rename = useMutation({
    mutationFn: (title: string) => renameCodeSession(session.id, title),
    onSettled: () => qc.invalidateQueries({ queryKey: ["code-sessions"] }),
  });

  function commit() {
    rename.mutate(draft.trim());
    onClose();
  }

  return (
    <form
      className="flex items-center gap-1 px-2 py-1 pl-8"
      onSubmit={(e) => {
        e.preventDefault();
        commit();
      }}
    >
      <input
        autoFocus
        className="field h-6 min-w-0 flex-1 px-1.5 text-xs"
        aria-label={t("code.sessions.renameField")}
        placeholder={t("code.sessions.renameHint")}
        title={session.title}
        maxLength={120}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
      />
      <button type="submit" aria-label={t("common.save")} className="text-accent">
        <Check className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        aria-label={t("common.cancel")}
        className="text-muted-foreground"
        onClick={onClose}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </form>
  );
}

/** Focus the sidebar row of ``sessionId`` once the editor that replaced it has gone. The row is
 *  found by its `data-session-row` attribute; ids are hex, so they need no escaping in a selector. */
export function focusSessionRow(sessionId: string) {
  window.setTimeout(() => {
    const row = document.querySelector<HTMLElement>(`[data-session-row="${sessionId}"]`);
    row?.focus();
  }, 0);
}
