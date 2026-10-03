import { useId } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { EyeOff, Pin, PinOff, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Tooltip } from "@/components/ui/tooltip";
import {
  flagCodeProject,
  grantCodeProjectShell,
  listCodeSessions,
  type CodeProject,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import { loadProjects, projectLabel, aliasesOf, sidebarOrder } from "@/lib/projects";

/**
 * The folders this install knows, and the three things decided about each: may the agent run
 * commands there, does it come first in the sidebar, and is it listed at all.
 *
 * The command switch is the SAME record the Code screen's "Commands" button writes and every turn is
 * held to on the server (`assemble_registry`). It used to live in that screen's browser storage,
 * which nothing outside the screen could see; here it can be read and revoked for every folder at
 * once, which is the point of putting it in Settings.
 *
 * Listed: every registered folder plus every folder a conversation was filed under — the same union
 * the sidebar shows, in the same order — so a folder you have worked in can be granted, pinned or
 * removed without first being "added".
 *
 * Not here: a "show in the file explorer" button. The window grants the page no native calls
 * (`src-tauri/capabilities/default.json`), and the only thing the shell hands to the operating
 * system is an http(s) link (`on_new_window` in `main.rs`). Opening a local folder would need a new
 * native command, and that is a decision about the window's privileges, not a button.
 */
export function FoldersCard({ reach }: { reach: string }) {
  const t = useT();
  const headingId = useId();
  const qc = useQueryClient();
  const projects = useQuery({ queryKey: ["code-projects"], queryFn: loadProjects });
  // Wrapped, not passed by reference: the card renders inside Settings, and a client function
  // that is missing (an older server's mock in a test, a module that failed to load) must fail
  // THIS query, not the whole Settings screen during render.
  const sessions = useQuery({ queryKey: ["code-sessions"], queryFn: () => listCodeSessions() });
  const rows = projects.data ?? [];
  const byPath = new Map(rows.map((row) => [row.path, row] as const));
  const aliases = aliasesOf(rows);
  // The empty workspace is "the app's own folder", not a folder anyone chose.
  const named = (sessions.data ?? []).filter((s) => s.workspace);
  const shown = sidebarOrder(named, rows, "").filter(Boolean);
  const hidden = rows.filter((row) => row.hidden);

  const keep = { onSuccess: (next: CodeProject[]) => qc.setQueryData(["code-projects"], next) };
  const grant = useMutation({
    mutationFn: ({ path, on }: { path: string; on: boolean }) => grantCodeProjectShell(path, on),
    ...keep,
  });
  const flag = useMutation({
    mutationFn: ({ path, ...flags }: { path: string; pinned?: boolean; hidden?: boolean }) =>
      flagCodeProject(path, flags),
    ...keep,
  });

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.folders")}
      </h2>
      <div className="divide-y divide-hairline">
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.folders.intro")}</p>
        {/* Said, not hidden: under these two standing reaches the switches below decide nothing,
            and a switch that reads "on" over a run that cannot use it is the lie this card is for
            removing. The server agrees — the grant never beats read_only. */}
        {reach === "read_only" ? (
          <p className="px-4 py-2.5 text-xs text-warn-foreground">{t("settings.folders.readOnly")}</p>
        ) : null}
        {reach === "workspace_shell" ? (
          <p className="px-4 py-2.5 text-xs text-muted-foreground">
            {t("settings.folders.everywhere")}
          </p>
        ) : null}
        {shown.length === 0 ? (
          <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.folders.empty")}</p>
        ) : (
          <ul className="divide-y divide-hairline">
            {shown.map((path) => {
              const row = byPath.get(path);
              const name = projectLabel(path, aliases);
              const pinned = row?.pinned === true;
              return (
                <li key={path} className="flex items-center gap-3 px-4 py-2.5">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm">{name}</div>
                    <div className="truncate font-mono text-xs text-muted-foreground" title={path}>
                      {path}
                    </div>
                    {row?.shell_granted && row.granted_at ? (
                      <div className="text-xs text-muted-foreground">
                        {t("settings.folders.grantedOn", { date: row.granted_at.slice(0, 10) })}
                      </div>
                    ) : null}
                  </div>
                  <Tooltip label={t(pinned ? "settings.folders.unpin" : "settings.folders.pin")}>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-pressed={pinned}
                      aria-label={t(pinned ? "settings.folders.unpin" : "settings.folders.pin")}
                      onClick={() => flag.mutate({ path, pinned: !pinned })}
                    >
                      {pinned ? <PinOff className="h-4 w-4" /> : <Pin className="h-4 w-4" />}
                    </Button>
                  </Tooltip>
                  <Tooltip label={t("settings.folders.hideHint")}>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label={t("settings.folders.hide")}
                      onClick={() => flag.mutate({ path, hidden: true })}
                    >
                      <EyeOff className="h-4 w-4" />
                    </Button>
                  </Tooltip>
                  <Switch
                    checked={row?.shell_granted === true}
                    label={t("settings.folders.commandsIn", { name })}
                    disabled={grant.isPending}
                    onChange={(on) => grant.mutate({ path, on })}
                  />
                </li>
              );
            })}
          </ul>
        )}
        {hidden.length ? (
          <div className="px-4 py-2.5">
            <div className="mb-1 text-xs font-medium text-muted-foreground">
              {t("settings.folders.hidden")}
            </div>
            <ul className="space-y-1">
              {hidden.map((row) => (
                <li key={row.path} className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate font-mono text-xs" title={row.path}>
                    {row.path}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => flag.mutate({ path: row.path, hidden: false })}
                  >
                    <RotateCcw className="h-3.5 w-3.5" /> {t("settings.folders.restore")}
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {grant.isError || flag.isError ? (
          <p className="px-4 py-2.5 text-xs text-bad-foreground">{t("settings.folders.saveError")}</p>
        ) : null}
      </div>
    </section>
  );
}
