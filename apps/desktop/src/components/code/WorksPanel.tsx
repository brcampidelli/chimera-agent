import { Loader2, Octagon, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import type { WorkInfo } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The conversation's background works: what was asked by voice and is being done on the strong
 * model while the talk goes on.
 *
 * One card per work, oldest first: its number (what the voice calls it), the request, the folder
 * it runs in, its state, how long it has run, what it touched, and — for one that ended — the
 * gist of its answer or the error. Two actions and only two, the same the talking model has as
 * tools: Stop (a queued work leaves the queue now; a running one ends at its next step — a model
 * call in flight ends first, and the card says so) and Undo (the receipt's own offer; gone once
 * taken or once the app restarts, and the button says which).
 *
 * The state comes over the conversation's live stream (`work_state` frames) while the screen is
 * open, and from the list on a reopen; the panel does not poll.
 */

const ACTIVE = new Set(["queued", "running", "waiting"]);

/** The state's word, by key — written out so the dictionary guard can see every key is used. */
const STATE_KEY: Record<string, string> = {
  queued: "code.works.state.queued",
  running: "code.works.state.running",
  waiting: "code.works.state.waiting",
  done: "code.works.state.done",
  failed: "code.works.state.failed",
  stopped: "code.works.state.stopped",
  undone: "code.works.state.undone",
};

function elapsed(work: WorkInfo, now: number): string {
  const from = work.started_at ?? work.created_at;
  const to = work.finished_at ?? now / 1000;
  const seconds = Math.max(0, Math.round(to - from));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, "0")}s`;
}

function folderName(path: string): string {
  return path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || path;
}

export function WorksPanel({
  works,
  onStop,
  onUndo,
}: {
  works: WorkInfo[];
  onStop: (work: WorkInfo) => Promise<void>;
  onUndo: (work: WorkInfo) => Promise<void>;
}) {
  const t = useT();
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState<string | null>(null);
  const anyActive = works.some((w) => ACTIVE.has(w.state));
  // The clock on a running card, once a second, only while something runs.
  useEffect(() => {
    if (!anyActive) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [anyActive]);

  if (works.length === 0) return null;

  return (
    <section className="space-y-2" aria-label={t("code.works.title")} data-testid="works-panel">
      <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {t("code.works.title")}
        <span className="text-muted-foreground/70">· {t("code.works.hint")}</span>
      </div>
      <ul className="space-y-1.5">
        {works.map((work) => {
          const active = ACTIVE.has(work.state);
          const tone =
            work.state === "failed"
              ? "text-bad-foreground"
              : work.state === "done"
                ? "text-ok-foreground"
                : work.state === "waiting"
                  ? "text-warn-foreground"
                  : "text-muted-foreground";
          return (
            <li
              key={work.id}
              className={cn(
                "rounded-card border border-hairline bg-surface-2/60 px-3 py-2 text-xs",
                active && "border-accent/40",
              )}
              data-testid="work-card"
              data-state={work.state}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-chip bg-accent/15 px-1.5 font-medium text-accent-ink">
                  {t("code.works.number", { n: work.number })}
                </span>
                <span className="min-w-0 flex-1 truncate font-medium text-foreground" title={work.title}>
                  {work.title}
                </span>
                <span className={cn("flex items-center gap-1", tone)} data-testid="work-state">
                  {work.state === "running" ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                  {t(STATE_KEY[work.state] ?? "code.works.state.running")}
                  {active || work.finished_at ? ` · ${elapsed(work, now)}` : ""}
                </span>
                {active ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy === work.id}
                    onClick={() => {
                      setBusy(work.id);
                      void onStop(work).finally(() => setBusy(null));
                    }}
                    data-testid="work-stop"
                  >
                    <Octagon className="h-3.5 w-3.5" /> {t("code.works.stop")}
                  </Button>
                ) : null}
                {!active && work.can_undo ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busy === work.id}
                    onClick={() => {
                      setBusy(work.id);
                      void onUndo(work).finally(() => setBusy(null));
                    }}
                    data-testid="work-undo"
                  >
                    <Undo2 className="h-3.5 w-3.5" /> {t("code.works.undo")}
                  </Button>
                ) : null}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground">
                <span>{t("code.works.folder", { name: folderName(work.workspace) })}</span>
                {work.model ? <span className="font-mono">{work.model.split("/").pop()}</span> : null}
                {work.tools > 0 ? <span>{t("code.works.tools", { n: work.tools })}</span> : null}
                {(work.edits ?? []).length > 0 ? (
                  <span title={(work.edits ?? []).join("\n")}>
                    {t("code.works.edits", { n: (work.edits ?? []).length })}
                  </span>
                ) : null}
                {work.verified ? <span>{t("code.works.verified", { state: work.verified })}</span> : null}
                {work.state === "waiting" ? <span>{t("code.works.waitingHint")}</span> : null}
                {work.state === "running" && busy === work.id ? <span>{t("code.works.stopping")}</span> : null}
              </div>
              {work.answer && !active ? (
                <p className="mt-1 whitespace-pre-wrap text-foreground/90" data-testid="work-answer">
                  {work.answer}
                </p>
              ) : null}
              {work.error ? (
                <p className="mt-1 text-bad-foreground" data-testid="work-error">
                  {work.error}
                </p>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
