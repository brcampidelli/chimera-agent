import { useEffect, useId, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { patchConfig } from "@/lib/api";
import { useT } from "@/lib/i18n";

const ARCHIVE_KEY = "CHIMERA_ARCHIVE_AFTER_DAYS";

/**
 * Settings › General › Conversations: archive a coding conversation nobody has touched for N days.
 *
 * `CHIMERA_ARCHIVE_AFTER_DAYS` shipped with the conversation states and no row, so the rule was off
 * for everyone who does not read `.env`. Empty is never, the shipped state. The server refuses zero
 * and anything that is not a positive number (`_check_archive_after_days`): zero would be saved,
 * shown, and archive nothing. The refusal shows under the row, in the server's words.
 *
 * The list reads the value on every look, so a save applies at once — there is no "applies" note.
 */
export function ConversationsCard({ archiveAfterDays }: { archiveAfterDays: number | null }) {
  const t = useT();
  const headingId = useId();
  const inputId = useId();
  const hintId = useId();
  const qc = useQueryClient();
  const saved = archiveAfterDays === null ? "" : String(archiveAfterDays);
  const [draft, setDraft] = useState(saved);
  useEffect(() => setDraft(saved), [saved]);
  const mutation = useMutation({
    mutationFn: (value: string) => patchConfig({ [ARCHIVE_KEY]: value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });
  const dirty = draft.trim() !== saved;

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.conversations")}
      </h2>
      <form
        className="space-y-1.5 px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate(draft.trim());
        }}
      >
        <div className="flex items-center justify-between gap-4">
          <label htmlFor={inputId} className="text-sm font-medium">
            {t("settings.row.archiveAfterDays")}
          </label>
          <span className="flex shrink-0 items-center gap-2 text-sm text-muted-foreground">
            <input
              id={inputId}
              inputMode="decimal"
              value={draft}
              placeholder={t("settings.archive.never")}
              aria-describedby={hintId}
              onChange={(e) => setDraft(e.target.value)}
              className="field h-8 w-20 px-2.5 text-sm text-foreground"
            />
            {t("settings.archive.days")}
            <Button type="submit" size="sm" variant="outline" disabled={!dirty || mutation.isPending}>
              {t("common.save")}
            </Button>
          </span>
        </div>
        <p id={hintId} className="text-xs text-muted-foreground">
          {t("settings.hint.archiveAfterDays")}
        </p>
        {mutation.isError && (
          <p role="alert" className="text-xs text-bad-foreground">
            {mutation.error instanceof Error ? mutation.error.message : String(mutation.error)}
          </p>
        )}
      </form>
    </section>
  );
}
