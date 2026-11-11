import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Square, X } from "lucide-react";

import { getDevServerLog, listDevServers, startDevServer, stopDevServer, type DevServer } from "@/lib/api";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The project's dev servers and the page the running one serves.
 *
 * The servers are the entries of the folder's own `.chimera/launch.json` (or `.claude/launch.json`):
 * this panel starts and stops them BY NAME, and the server decides whether commands are on for the
 * folder (it refuses otherwise, and says why, which is shown here before anyone clicks). Nothing typed
 * on this screen becomes a command.
 *
 * **The frame has NO `allow-same-origin`, on purpose.** The project's server is on this computer on
 * another port, but cookies are shared across ports of one host, and the app's own session cookie (a
 * paired browser tab's) is host-wide: a page that kept its own origin could read it and send it to its
 * server. So the page runs in an opaque origin, like the HTML preview beside it: its scripts run, it
 * gets no storage or cookies of its own, and a page that needs them is one link away in the person's
 * browser ("Open in browser"). An isolated origin that kept storage would need its own host name, which
 * a plain `localhost` server does not have.
 *
 * **Nothing is framed until it is asked for.** A server shows here only after a click on it (Start,
 * Show, or Show for one already running at a `url`), never because it happens to be listening, and never
 * when its port is the app's own (`sharesAppOrigin`, by PORT: a second host name for the same port is
 * still the same server).
 */

/** The page is the app's own when the PORT matches, whatever the host is called. */
export function sharesAppOrigin(url: string, here: Pick<Location, "port"> = window.location): boolean {
  try {
    return new URL(url).port === here.port;
  } catch {
    return true; // an address that cannot be read is not framed
  }
}

function LogTail({ workspace, name }: { workspace: string | null; name: string }) {
  const t = useT();
  const log = useQuery({
    queryKey: ["dev-server-log", workspace, name],
    queryFn: () => getDevServerLog(workspace, name),
    refetchInterval: 2000,
  });
  return (
    <details className="mt-1">
      <summary className={cn("cursor-pointer text-xs text-muted-foreground", focusRing)}>{t("code.devserver.log")}</summary>
      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-chip bg-surface-2 p-1.5 font-mono text-xs">
        {(log.data?.lines ?? []).join("\n")}
      </pre>
    </details>
  );
}

function ServerRow({
  server,
  workspace,
  canStart,
  onOpen,
}: {
  server: DevServer;
  workspace: string | null;
  canStart: boolean;
  onOpen: (name: string) => void;
}) {
  const t = useT();
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["dev-servers", workspace] });
  const start = useMutation({
    mutationFn: () => startDevServer(workspace, server.name),
    onSuccess: () => onOpen(server.name),
    onSettled: refresh,
  });
  const stop = useMutation({ mutationFn: () => stopDevServer(workspace, server.name), onSettled: refresh });
  const live = server.state === "starting" || server.state === "running";
  return (
    <li className="rounded-chip border border-border p-2" data-testid={`dev-server-${server.name}`}>
      <div className="flex min-w-0 items-center gap-2 text-sm">
        <span className="min-w-0 truncate font-mono">{server.name}</span>
        <span className="shrink-0 text-xs text-muted-foreground">{t("code.devserver.port", { port: server.port })}</span>
        <span
          className={cn(
            "shrink-0 text-xs",
            server.state === "running" ? "text-ok-foreground" : server.state === "failed" ? "text-bad-foreground" : "text-muted-foreground",
          )}
        >
          {server.state === "stopped"
            ? t("code.devserver.state.stopped")
            : server.state === "starting"
              ? t("code.devserver.state.starting")
              : server.state === "running"
                ? t("code.devserver.state.running")
                : server.state === "exited"
                  ? t("code.devserver.state.exited")
                  : server.state === "timed_out"
                    ? t("code.devserver.state.timed_out", { hours: server.limit_hours ?? 0 })
                    : t("code.devserver.state.failed")}
        </span>
        {server.kind === "process" && server.state === "running" ? (
          <button
            type="button"
            onClick={() => onOpen(server.name)}
            className={cn("ml-auto shrink-0 rounded-chip px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-surface-2 hover:text-foreground", focusRing)}
          >
            {t("code.devserver.show")}
          </button>
        ) : null}
        {server.kind === "attach" ? (
          <button
            type="button"
            disabled={start.isPending}
            onClick={() => start.mutate()}
            className={cn("ml-auto shrink-0 rounded-chip px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-surface-2 hover:text-foreground disabled:opacity-50", focusRing)}
          >
            {t("code.devserver.show")}
          </button>
        ) : null}
        {server.kind === "process" ? (
          <button
            type="button"
            disabled={live ? stop.isPending : !canStart || start.isPending}
            onClick={() => (live ? stop.mutate() : start.mutate())}
            className={cn(
              "inline-flex shrink-0 items-center gap-1 rounded-chip px-1.5 py-0.5 text-xs",
              server.state !== "running" && "ml-auto",
              "text-muted-foreground transition hover:bg-surface-2 hover:text-foreground disabled:opacity-50",
              focusRing,
            )}
          >
            {live ? <Square className="h-3 w-3" aria-hidden="true" /> : <Play className="h-3 w-3" aria-hidden="true" />}
            {live ? t("code.devserver.stop") : t("code.devserver.start")}
          </button>
        ) : null}
      </div>
      {server.command ? (
        <p className="mt-0.5 break-all font-mono text-xs text-muted-foreground" data-testid={`dev-server-command-${server.name}`}>
          {server.command}
          <span className="block">{t("code.devserver.cwd", { cwd: server.cwd })}</span>
        </p>
      ) : null}
      {start.error ? <p className="mt-1 text-xs text-bad-foreground">{String((start.error as Error).message)}</p> : null}
      {server.kind === "process" && server.state !== "stopped" ? <LogTail workspace={workspace} name={server.name} /> : null}
    </li>
  );
}

export function PreviewPane({ workspace, onClose }: { workspace: string; onClose: () => void }) {
  const t = useT();
  const q = useQuery({
    queryKey: ["dev-servers", workspace],
    queryFn: () => listDevServers(workspace || null),
    refetchInterval: (query) =>
      query.state.data?.servers.some((s) => s.state === "starting" || s.state === "running") ? 2000 : 10000,
  });
  const data = q.data;
  // Only a server the person clicked, and only while it is up (`opened`).
  const [opened, setOpened] = useState<string | null>(null);
  const showing = data?.servers.find(
    (s) => s.name === opened && s.state === "running" && s.url && !sharesAppOrigin(s.url),
  );

  return (
    <section className="flex min-h-0 flex-1 flex-col" data-panel="preview" aria-label={t("code.devserver.title")}>
      <div className="flex items-center gap-2 border-b border-hairline px-4 py-2.5">
        <span className="min-w-0 flex-1 truncate text-xs uppercase tracking-wider text-muted-foreground">
          {t("code.devserver.title")}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label={t("code.devserver.close")}
          className={cn("rounded-chip p-1 text-muted-foreground hover:text-foreground", focusRing)}
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="space-y-2 border-b border-hairline px-4 py-2">
        {data && !data.found ? <p className="text-xs text-muted-foreground">{t("code.devserver.none")}</p> : null}
        {data && data.errors.length > 0 ? (
          <div className="text-xs text-bad-foreground" role="alert">
            <p>{t("code.devserver.invalid")}</p>
            <ul className="list-disc pl-4">
              {data.errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {data && data.found && data.errors.length === 0 && !data.can_start ? (
          <p className="text-xs text-muted-foreground">{t("code.devserver.commandsOff")}</p>
        ) : null}
        {data && data.config_changed.length > 0 ? (
          <p className="text-xs text-warn-foreground" role="alert">
            {t("code.devserver.configChanged", { files: data.config_changed.join(", ") })}
          </p>
        ) : null}
        {data?.warnings.map((w) => (
          <p key={w} className="text-xs text-warn-foreground">{w}</p>
        ))}
        {data && data.servers.length > 0 ? (
          <ul className="space-y-1.5">
            {data.servers.map((s) => (
              <ServerRow key={s.name} server={s} workspace={workspace || null} canStart={data.can_start} onOpen={setOpened} />
            ))}
          </ul>
        ) : null}
      </div>
      {showing?.url ? (
        <>
          <iframe
            key={showing.url}
            title={t("code.devserver.frameTitle", { name: showing.name })}
            // Opaque origin: no `allow-same-origin` (cookies are shared across ports of a host). See
            // the comment on this file.
            sandbox="allow-scripts allow-forms allow-popups allow-modals"
            src={showing.url}
            className="min-h-0 w-full flex-1 border-0 bg-white"
          />
          <p className="border-t border-hairline px-3 py-1.5 text-xs text-muted-foreground">
            {t("code.devserver.sandboxNote")}{" "}
            <a href={showing.url} target="_blank" rel="noopener noreferrer" className={cn("underline", focusRing)}>
              {t("code.devserver.openOutside")}
            </a>
          </p>
        </>
      ) : (
        <p className="px-4 py-6 text-sm text-muted-foreground">{t("code.devserver.idle")}</p>
      )}
    </section>
  );
}
