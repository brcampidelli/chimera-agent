import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";

import { Lifecycle } from "@/components/lifecycle/Lifecycle";
import { CrewLauncher } from "@/components/orchestration/CrewLauncher";
import { Orchestration } from "@/components/orchestration/Orchestration";
import { RunLauncher } from "@/components/run/RunLauncher";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import { DEFAULT_MODE, MODES, usesVerify, type WorkMode } from "./modes";

/** The example under the task box changes with the mode, because the modes are for different work.
 *
 *  Reusing each screen's own placeholder rather than writing a fifth: the sentence that taught you
 *  what the lifecycle was for is the same sentence that should teach you now, and a new one would
 *  be a second description of one thing, drifting from the first in nine translations. */
const PLACEHOLDER: Record<WorkMode, string> = {
  single: "runs.taskPlaceholder",
  lifecycle: "lifecycle.taskPlaceholder",
  hierarchy: "orch.task.placeholder",
  crew: "crew.task.placeholder",
};

/**
 * One place to type, and buttons for what happens to what you typed.
 *
 * Four ways to run a task used to be four screens, and each one asked for the task again. Which
 * meant the choice came first — you picked "Lifecycle" before writing a word — and changing your
 * mind cost retyping. The order was backwards: the task is the thing that decides how it should be
 * run, so it is written once, at the top, and the mode is chosen beside it.
 *
 * **What is shared is shared because it is the same thing.** The task is the task in all four. The
 * check command is a shell command run against the result in three of them, so it travels too. What
 * does not travel is what genuinely differs — attempts, roles, worker slots — and those stay inside
 * the mode that means them. Sharing a field that means something different in the next mode is how
 * a form starts lying, so `hierarchy` says out loud that it is dropping the check rather than
 * quietly not sending it.
 *
 * **Modes lock while something is live.** Switching would unmount the running mode, and unmounting
 * the run kills its stream while the tokens keep being spent — the same money, no longer watched.
 * It is a new restriction on paper and none in practice: leaving these screens by switching tabs
 * already did exactly that, silently.
 */
export function TaskConsole({
  workspace,
  onOpenCode,
  initialMode = DEFAULT_MODE,
  onMode,
}: {
  workspace: string;
  onOpenCode: () => void;
  /** The mode to open in. Uncontrolled from there on, so the screen can change its own mode — the
   *  fallback note does exactly that when a preview says the task is write-shaped. */
  initialMode?: WorkMode;
  /** Told about every change, so the shell can put it in the URL. The console does not write the
   *  URL itself: `setParams` replaces the whole query, and this screen's tab lives in there too. */
  onMode?: (mode: WorkMode) => void;
}) {
  const t = useT();
  const [mode, setMode] = useState<WorkMode>(initialMode);
  const [task, setTask] = useState("");
  const [verify, setVerify] = useState("");
  // Reported by whichever mode is mounted. One flag and not one per mode: only one can be mounted,
  // so a second flag could only ever disagree with this one.
  const [busy, setBusy] = useState(false);

  const choose = useCallback(
    (next: WorkMode) => {
      setMode(next);
      onMode?.(next);
    },
    [onMode],
  );

  // A mode that ends its run leaves `busy` true unless somebody clears it, and the flag is owned by
  // the child that just unmounted. Clearing on every change is the one place that cannot forget.
  useEffect(() => {
    setBusy(false);
  }, [mode]);

  const shared = { task, verify, workspace, onBusy: setBusy };

  return (
    <div className="space-y-5">
      <ModeStrip mode={mode} onChange={choose} disabled={busy} />

      <p className="text-xs text-muted-foreground">{t(`work.mode.${mode}.what`)}</p>
      {busy ? <p className="text-xs text-warn-foreground">{t("work.busy")}</p> : null}

      <div className="space-y-2">
        <div>
          <label
            className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
            htmlFor="work-task"
          >
            {t("orch.task.label")}
          </label>
          <textarea
            id="work-task"
            value={task}
            onChange={(event) => setTask(event.target.value)}
            rows={3}
            placeholder={t(PLACEHOLDER[mode])}
            disabled={busy}
            className="mt-1 w-full resize-y rounded-card border border-hairline bg-surface-2/40 p-3 text-sm text-foreground placeholder:text-muted-foreground"
          />
        </div>

        {usesVerify(mode) ? (
          <div>
            <label
              className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
              htmlFor="work-verify"
            >
              {t("crew.verify.label")}
            </label>
            <input
              id="work-verify"
              value={verify}
              onChange={(event) => setVerify(event.target.value)}
              placeholder={t("runs.verifyPlaceholder")}
              disabled={busy}
              className="mt-1 w-full rounded-card border border-hairline bg-surface-2/40 p-2 font-mono text-xs text-foreground placeholder:text-muted-foreground"
            />
            {/* Only the crew, and not because the others do not care. In a run or a lifecycle an
                empty check means a model reads the answer, and the server says so in a frame the
                moment the run starts — a sentence backed by what actually resolved, which beats a
                guess made here beforehand. The crew has no such frame and a worse failure: with
                nothing able to fail, every worker merges, and workers that touched one file all
                lose it. That is the case where saying nothing produces a run that lands nothing. */}
            {mode === "crew" ? (
              <p className="mt-1 text-xs text-muted-foreground">
                {verify.trim() ? t("crew.verify.why") : t("crew.verify.missing")}
              </p>
            ) : null}
          </div>
        ) : verify.trim() ? (
          // Said, rather than silently dropped. The field vanishing with text in it looks like the
          // text went somewhere; this is the one mode where it does not go anywhere, and a form
          // that implies it sent something it did not send is worse than one that asks twice.
          <p className="text-xs text-warn-foreground">{t("work.verifyIgnored")}</p>
        ) : null}

        {/* The project comes from where a project is chosen, and is shown rather than asked for.
            A second folder field would be a second answer to one question. */}
        <p className="truncate font-mono text-xs text-muted-foreground" title={workspace}>
          {workspace || t("code.sessions.defaultProject")}
        </p>
      </div>

      {mode === "single" ? (
        <RunLauncher {...shared} />
      ) : mode === "lifecycle" ? (
        <Lifecycle {...shared} />
      ) : mode === "hierarchy" ? (
        <Orchestration
          task={task}
          workspace={workspace}
          onBusy={setBusy}
          onOpenCode={onOpenCode}
          // The preview's own verdict, wired to the control it is a verdict about. It reads "this
          // task writes files — that goes to a crew", and until now saying so was all it could do.
          onCrew={() => choose("crew")}
        />
      ) : (
        <CrewLauncher {...shared} />
      )}
    </div>
  );
}

/** Four buttons, one of which is on. A radiogroup and not a tab strip: these do not swap panels,
 *  they change what the button below is about to do to the text above. */
function ModeStrip({
  mode,
  onChange,
  disabled,
}: {
  mode: WorkMode;
  onChange: (mode: WorkMode) => void;
  disabled: boolean;
}) {
  const t = useT();
  const refs = useRef(new Map<WorkMode, HTMLButtonElement>());

  // Arrow keys move within the group and the group is one tab stop, same as the tab strip above it.
  // Without this each mode is its own stop and a keyboard user presses Tab four times to reach the
  // task box — on the screen whose whole point is that the task box comes first.
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key) || disabled) return;
    event.preventDefault();
    const i = MODES.indexOf(mode);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? MODES.length - 1
          : (i + (event.key === "ArrowRight" ? 1 : -1) + MODES.length) % MODES.length;
    onChange(MODES[next]);
    refs.current.get(MODES[next])?.focus();
  };

  return (
    <div
      role="radiogroup"
      aria-label={t("work.mode.label")}
      onKeyDown={onKeyDown}
      className="flex flex-wrap items-center gap-1.5"
    >
      {MODES.map((value) => {
        const on = value === mode;
        return (
          <button
            key={value}
            ref={(el) => {
              if (el) refs.current.set(value, el);
              else refs.current.delete(value);
            }}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            disabled={disabled}
            onClick={() => onChange(value)}
            className={cn(
              "rounded-chip border px-3 py-1.5 text-xs transition-colors duration-1 ease-out",
              "disabled:cursor-not-allowed disabled:opacity-50",
              focusRing,
              on
                ? "border-accent bg-accent/10 text-foreground"
                : "border-hairline text-muted-foreground hover:text-foreground",
            )}
          >
            {t(`work.mode.${value}`)}
          </button>
        );
      })}
    </div>
  );
}
