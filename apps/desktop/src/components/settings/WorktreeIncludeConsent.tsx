import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Row } from "@/components/settings/rows";
import { allowWorktreeInclude, getWorktreeIncludePending } from "@/lib/api";
import { useT } from "@/lib/i18n";

const PENDING_KEY = ["worktree-include-pending"] as const;

/** Names shown before the owner asks for the rest. */
const FIRST_SHOWN = 20;

/**
 * The owner's yes to a project's `.worktreeinclude` (`chimera/core/worktree_include_consent.py`).
 * The file comes from the repository and names ignored files (`.env`, a local database) to copy into
 * every new conversation worktree, so the first time a project's file is met, and each time it
 * changes, nothing is copied: the files it would copy wait here for an answer. Only this screen can
 * answer; the bridge and a paired device can only make the request appear.
 */
export function WorktreeIncludeConsent() {
  const t = useT();
  const qc = useQueryClient();
  const pending = useQuery({ queryKey: PENDING_KEY, queryFn: () => getWorktreeIncludePending() });
  const allow = useMutation({
    mutationFn: ({ project, digest }: { project: string; digest: string }) =>
      allowWorktreeInclude(project, digest),
    onSuccess: (next) => qc.setQueryData(PENDING_KEY, next),
    // A 409 means the file changed since it was shown: read the list again and show the new version.
    onError: () => qc.invalidateQueries({ queryKey: PENDING_KEY }),
  });
  const items = pending.data?.pending ?? [];
  // Projects whose whole list the owner opened: what is agreed to is what was seen.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  return (
    <>
      <Row
        label={t("settings.row.worktreeInclude")}
        hint={t("settings.hint.worktreeInclude")}
      >
        <span className="text-xs text-muted-foreground">
          {items.length === 0 ? t("settings.worktreeInclude.none") : ""}
        </span>
      </Row>
      {items.map((item) => (
        <div key={`${item.project}:${item.digest}`} className="flex flex-col gap-2 px-4 pb-3">
          <p className="text-sm">{t("settings.worktreeInclude.wants", { project: item.project })}</p>
          <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
            {(expanded.has(item.project) ? item.files : item.files.slice(0, FIRST_SHOWN)).map((file) => (
              <li key={file} className="truncate font-mono">
                {file}
              </li>
            ))}
            {item.more > 0 ? <li>{t("settings.worktreeInclude.more", { count: item.more })}</li> : null}
          </ul>
          <div className="flex items-center gap-2">
            {item.files.length > FIRST_SHOWN && !expanded.has(item.project) ? (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setExpanded(new Set([...expanded, item.project]))}
              >
                {t("settings.worktreeInclude.showAll", { count: item.files.length })}
              </Button>
            ) : null}
            <Button
              size="sm"
              variant="outline"
              disabled={allow.isPending}
              aria-label={t("settings.worktreeInclude.allowFor", { project: item.project })}
              onClick={() => allow.mutate({ project: item.project, digest: item.digest })}
            >
              {t("settings.worktreeInclude.allow")}
            </Button>
          </div>
        </div>
      ))}
    </>
  );
}
