import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Panel } from "@/components/ui/panel";
import { streamRun, type RunEvent } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const fieldCls = "field w-full px-3 text-sm";

/**
 * Start an autonomous run.
 *
 * There were three of these: one in Runs, one inside Code's panel, one in Agents. All doing the
 * same thing — a task, an optional verify command, an attempt budget — and all drifting apart,
 * because the only thing holding them in sync was whoever remembered to edit all three.
 *
 * They converge on code, not on a screen. `variant="inline"` is what lets Code keep its run scoped
 * to the workspace it already has open instead of asking for one.
 */
export function RunLauncher({
  variant = "panel",
  workspace,
  onLine,
  onFinished,
}: {
  variant?: "panel" | "inline";
  /** Fixed workspace. When given, the field is hidden — Code already knows where it is working. */
  workspace?: string;
  /** Each human-readable progress line, already formatted. */
  onLine?: (line: string) => void;
  onFinished?: (success: boolean) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [task, setTask] = useState("");
  const [verify, setVerify] = useState("");
  const [ws, setWs] = useState("");
  const [maxAttempts, setMaxAttempts] = useState(3);
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<string[]>([]);

  const append = (s: string) => {
    setLines((prev) => [...prev, s]);
    onLine?.(s);
  };

  function start() {
    if (!task.trim() || running) return;
    setRunning(true);
    setLines([]);
    const finish = (success: boolean) => {
      setRunning(false);
      onFinished?.(success);
      void qc.invalidateQueries({ queryKey: ["runs"] });
    };
    void streamRun(
      {
        task: task.trim(),
        verify: verify.trim() || null,
        workspace: workspace ?? (ws.trim() || null),
        max_attempts: maxAttempts,
      },
      {
        onEvent: (e) => {
          const line = liveLine(e, t);
          if (line) append(line);
        },
        onDone: (d) => {
          append(d.success ? t("runs.doneOk") : t("runs.doneFail"));
          finish(d.success);
        },
        onError: () => {
          append(t("runs.doneFail"));
          finish(false);
        },
      },
    );
  }

  const body = (
    <div className={cn("space-y-2.5", variant === "panel" && "px-4 py-3")}>
      <textarea
        className={cn(fieldCls, "min-h-[72px] resize-y py-2")}
        placeholder={t("runs.taskPlaceholder")}
        aria-label={t("runs.taskPlaceholder")}
        value={task}
        onChange={(e) => setTask(e.target.value)}
        disabled={running}
      />
      <input
        className={cn(fieldCls, "h-9 font-mono text-xs")}
        placeholder={t("runs.verifyPlaceholder")}
        aria-label={t("runs.verifyPlaceholder")}
        value={verify}
        onChange={(e) => setVerify(e.target.value)}
        disabled={running}
      />
      {workspace === undefined && (
        <input
          className={cn(fieldCls, "h-9 font-mono text-xs")}
          placeholder={t("runs.workspacePlaceholder")}
          aria-label={t("runs.workspacePlaceholder")}
          value={ws}
          onChange={(e) => setWs(e.target.value)}
          disabled={running}
        />
      )}
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {t("runs.maxAttempts")}
          <input
            type="number"
            min={1}
            max={10}
            className="field h-9 w-16 px-2 text-sm"
            value={maxAttempts}
            onChange={(e) => setMaxAttempts(Math.min(10, Math.max(1, Number(e.target.value) || 1)))}
            disabled={running}
          />
        </label>
        <Button size="sm" disabled={!task.trim() || running} onClick={start}>
          {running ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> {t("runs.running")}
            </>
          ) : (
            <>
              <Play className="h-4 w-4" /> {t("runs.run")}
            </>
          )}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t("runs.safetyNote")}</p>
      <RunStream lines={lines} />
    </div>
  );

  return variant === "panel" ? <Panel title={t("runs.new")}>{body}</Panel> : body;
}

/** The live progress of a run. Separate so Code can show it in its own column. */
export function RunStream({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <div
      // A run's own narration of what it is doing. `role="log"` so it accumulates rather than
      // interrupting; polite, because it is progress, not an alert.
      role="log"
      aria-live="polite"
      className="mt-1 space-y-1 rounded-chip bg-surface-2 p-2 font-mono text-xs text-muted-foreground"
    >
      {lines.map((line, i) => (
        <div key={i}>{line}</div>
      ))}
    </div>
  );
}

/** Turn one SSE event into a line a person can read. Exported so Runs and Code share the wording. */
export function liveLine(e: RunEvent, t: TFunc): string | null {
  if (e.kind === "status") return /planning/i.test(e.text) ? t("runs.planning") : e.text;
  if (e.kind === "attempt") return `${t("runs.attempt")} ${e.index} — ${t("runs.verifying")}`;
  if (e.kind === "result")
    return `${t("runs.attempt")} ${e.index}: ${e.success ? t("runs.passed") : t("runs.failed")}`;
  return null; // `final` is covered by onDone
}
