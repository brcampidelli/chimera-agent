import { useCallback, useEffect, useRef, useState } from "react";
import { Square, Trash2 } from "lucide-react";

import { cancelExec, streamExec } from "@/lib/api";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";

/**
 * The command runner, next to the file you are editing.
 *
 * **It is not a terminal, and it says so.** There is no PTY: each command is a fresh subprocess, so
 * `cd` and `export` do not persist, nothing interactive works, and a program that asks a question
 * gets EOF. That is a real limitation and the panel states it rather than letting people find out by
 * losing an hour to a prompt they cannot answer. The alternative — a real PTY — was cut from the
 * plan on purpose; half a terminal that looks like a whole one is worse than an honest runner.
 *
 * **Stop actually stops.** It asks the server to kill the process tree, not just the shell we hold:
 * a command is usually a launcher, and killing the launcher leaves the port busy while the panel
 * claims the command ended. Closing the panel does the same thing, because the stream ending is the
 * server's other signal to kill — otherwise a `npm run dev` started here would hold its port until
 * the timeout, which for a long command is an hour.
 *
 * History is per workspace and survives a reload, because the value of a runner is mostly in not
 * retyping the same four commands.
 */

const HISTORY_LIMIT = 50;

function historyKey(workspace: string | null): string {
  return `chimera.runner.history:${workspace ?? "default"}`;
}

function loadHistory(workspace: string | null): string[] {
  try {
    const raw = localStorage.getItem(historyKey(workspace));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

interface Line {
  text: string;
  /** Marks the command itself and the exit line, so the output reads as a transcript. */
  kind: "command" | "output" | "exit";
}

export function Runner({ workspace }: { workspace: string | null }) {
  const t = useT();
  const [command, setCommand] = useState("");
  const [lines, setLines] = useState<Line[]>([]);
  const [history, setHistory] = useState<string[]>(() => loadHistory(workspace));
  // Where the up-arrow is in the history. -1 means "editing a new command", not "at the newest
  // entry" — the difference is whether pressing Down returns you to what you had typed.
  const [cursor, setCursor] = useState(-1);
  const [running, setRunning] = useState(false);
  const runId = useRef<string | null>(null);
  const abort = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    setHistory(loadHistory(workspace));
  }, [workspace]);

  // Follow the output, the way a terminal does.
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [lines]);

  // Leaving the screen must stop the command. The server kills on stream end too; this is the half
  // that does not depend on the browser's disconnect being noticed.
  useEffect(
    () => () => {
      abort.current?.abort();
      if (runId.current) void cancelExec(runId.current).catch(() => {});
    },
    [],
  );

  const remember = useCallback(
    (entry: string) => {
      setHistory((prev) => {
        const next = [entry, ...prev.filter((item) => item !== entry)].slice(0, HISTORY_LIMIT);
        try {
          localStorage.setItem(historyKey(workspace), JSON.stringify(next));
        } catch {
          // A full or blocked localStorage costs history, not the runner.
        }
        return next;
      });
    },
    [workspace],
  );

  const run = useCallback(async () => {
    const entry = command.trim();
    if (!entry || running) return;
    remember(entry);
    setCommand("");
    setCursor(-1);
    setLines((prev) => [...prev, { text: entry, kind: "command" }]);
    setRunning(true);
    const controller = new AbortController();
    abort.current = controller;
    await streamExec(
      { command: entry, workspace },
      {
        onStarted: (id) => {
          runId.current = id;
        },
        onLine: (text) => setLines((prev) => [...prev, { text, kind: "output" }]),
        onExit: (code) =>
          setLines((prev) => [...prev, { text: t("runner.exit", { code: String(code) }), kind: "exit" }]),
        onError: (message) => setLines((prev) => [...prev, { text: message, kind: "exit" }]),
      },
      controller.signal,
    );
    runId.current = null;
    setRunning(false);
  }, [command, running, workspace, remember, t]);

  const stop = useCallback(async () => {
    const id = runId.current;
    if (id) await cancelExec(id).catch(() => {});
    abort.current?.abort();
    runId.current = null;
    setRunning(false);
  }, []);

  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void run();
      return;
    }
    if (event.key === "ArrowUp" && history.length) {
      event.preventDefault();
      const next = Math.min(cursor + 1, history.length - 1);
      setCursor(next);
      setCommand(history[next]);
      return;
    }
    if (event.key === "ArrowDown" && cursor >= 0) {
      event.preventDefault();
      const next = cursor - 1;
      setCursor(next);
      setCommand(next < 0 ? "" : history[next]);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col border-t border-hairline bg-card/40">
      <div className="flex shrink-0 items-center justify-between gap-2 px-3 py-1">
        <span className="text-xs text-muted-foreground">{t("runner.title")}</span>
        <span className="flex items-center gap-1">
          {running && (
            <button
              type="button"
              onClick={() => void stop()}
              className={cn(
                "flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-bad-foreground hover:bg-surface-hover",
                focusRing,
              )}
            >
              <Square className="h-3 w-3" />
              {t("runner.stop")}
            </button>
          )}
          <button
            type="button"
            onClick={() => setLines([])}
            aria-label={t("runner.clear")}
            className={cn(
              "flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-surface-hover hover:text-foreground",
              focusRing,
            )}
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-3 pb-1 font-mono text-xs">
        {lines.length === 0 ? (
          // The limitation, where someone will read it BEFORE typing `vim` or `cd ..`.
          <p className="py-2 font-sans text-xs text-muted-foreground">{t("runner.empty")}</p>
        ) : (
          lines.map((line, index) => (
            <div
              key={index}
              className={cn(
                "whitespace-pre-wrap break-words",
                line.kind === "command" && "text-accent",
                line.kind === "exit" && "text-muted-foreground",
              )}
            >
              {line.kind === "command" ? `$ ${line.text}` : line.text}
            </div>
          ))
        )}
        <div ref={bottom} />
      </div>

      <div className="flex shrink-0 items-center gap-2 border-t border-hairline px-3 py-1.5">
        <span aria-hidden className="font-mono text-xs text-muted-foreground">
          $
        </span>
        <input
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          onKeyDown={onKeyDown}
          disabled={running}
          aria-label={t("runner.input")}
          placeholder={t("runner.placeholder")}
          className={cn(
            "min-w-0 flex-1 bg-transparent font-mono text-xs text-foreground outline-none",
            "placeholder:text-muted-foreground disabled:opacity-50",
          )}
        />
      </div>
    </div>
  );
}
