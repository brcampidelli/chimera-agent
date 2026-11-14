import { CornerDownLeft, Play, SlidersHorizontal, X } from "lucide-react";
import { useState } from "react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { START_ALL_LIMIT, type TaskCardState } from "@/lib/taskSuggestions";
import { cn } from "@/lib/utils";

/** The owner's options for starting one card. Posture is never here: the project's carries over. */
export interface TaskStartOptions {
  model: string;
  newWorktree: boolean;
}

const DEFAULT_OPTIONS: TaskStartOptions = { model: "", newWorktree: false };

const BUTTON = cn(
  "flex items-center gap-1.5 rounded-chip border border-hairline bg-surface-2 px-3 py-1.5 text-xs",
  "text-muted-foreground transition-colors duration-1 ease-out hover:border-accent hover:text-foreground",
  "disabled:cursor-not-allowed disabled:opacity-60",
  focusRing,
);

/**
 * The cards the agent proposed in this turn (`suggest_task`). Each is a proposal: nothing starts until
 * the owner clicks Start, Start all (after a confirmation that names the count), or Do it here. The full
 * prompt is shown before any of those, because it is what the new conversation will run. Dismiss only
 * removes the card from view.
 *
 * `busy` means a turn is running in this conversation: "Do it here" would queue behind it, so it is
 * disabled and says why, and the card stays until the owner sends it.
 */
export function TaskSuggestionCards({
  cards,
  busy = false,
  onStart,
  onStartAll,
  onHere,
  onDismiss,
}: {
  cards: TaskCardState[];
  busy?: boolean;
  onStart: (card: TaskCardState, options: TaskStartOptions) => void;
  onStartAll: (cards: TaskCardState[]) => void;
  onHere: (card: TaskCardState) => void;
  onDismiss: (card: TaskCardState) => void;
}) {
  const t = useT();
  const [openId, setOpenId] = useState<string | null>(null);
  const [promptId, setPromptId] = useState<string | null>(null);
  const [confirmAll, setConfirmAll] = useState(false);
  const [options, setOptions] = useState<Record<string, TaskStartOptions>>({});
  if (cards.length === 0) return null;
  const waiting = cards.filter((c) => c.status === undefined || c.status === "failed");
  const batch = waiting.slice(0, START_ALL_LIMIT);

  return (
    <section
      aria-label={t("code.task.label")}
      className="flex flex-col gap-2"
      data-testid="task-suggestion-cards"
    >
      {waiting.length > 1 ? (
        <div className="flex flex-wrap items-center justify-end gap-2 text-xs">
          {confirmAll ? (
            <>
              <span className="text-muted-foreground">
                {t("code.task.startAllConfirm", { count: batch.length })} {t("code.task.startAllLimit", { limit: START_ALL_LIMIT })}
              </span>
              <button
                type="button"
                className={BUTTON}
                onClick={() => {
                  setConfirmAll(false);
                  onStartAll(batch);
                }}
              >
                {t("code.task.confirm")}
              </button>
              <button type="button" className={BUTTON} onClick={() => setConfirmAll(false)}>
                {t("code.task.cancel")}
              </button>
            </>
          ) : (
            <button
              type="button"
              className={BUTTON}
              disabled={busy}
              onClick={() => setConfirmAll(true)}
            >
              <Play className="h-3 w-3" aria-hidden />
              {t("code.task.startAll")}
            </button>
          )}
        </div>
      ) : null}
      {cards.map((card) => {
        const opts = options[card.id] ?? DEFAULT_OPTIONS;
        const starting = card.status === "starting";
        const open = openId === card.id;
        const showPrompt = promptId === card.id;
        const actionable = card.status !== "started";
        return (
          <article
            key={card.id}
            data-testid="task-suggestion-card"
            className="rounded-xl border border-hairline bg-surface-2 p-3 text-sm"
          >
            <h4 className="font-medium text-foreground">{card.title}</h4>
            <p className="mt-1 text-xs text-muted-foreground">{card.tldr}</p>
            {card.tainted ? (
              <p role="note" className="mt-2 text-xs text-warn-foreground">
                {t("code.task.taintedWarning")}
              </p>
            ) : null}
            <div className="mt-2">
              <button
                type="button"
                className={BUTTON}
                aria-expanded={showPrompt}
                onClick={() => setPromptId(showPrompt ? null : card.id)}
              >
                {t("code.task.promptToggle")}
              </button>
              {showPrompt ? (
                <div className="mt-2 flex flex-col gap-1">
                  <span className="text-xs text-muted-foreground">{t("code.task.promptLabel")}</span>
                  <pre
                    data-testid="task-suggestion-prompt"
                    className="max-h-64 overflow-auto whitespace-pre-wrap rounded-chip border border-hairline bg-surface-2 p-2 text-xs"
                  >
                    {card.prompt}
                  </pre>
                </div>
              ) : null}
            </div>
            {starting && card.fetchingBase ? (
              <p role="status" className="mt-2 text-xs text-muted-foreground">
                {t("code.task.fetchingBase")}
              </p>
            ) : null}
            {card.status === "started" ? (
              <p className="mt-2 text-xs text-muted-foreground">{t("code.task.started")}</p>
            ) : null}
            {card.status === "started" && card.baseWarning ? (
              <p role="alert" className="mt-1 text-xs font-medium text-warn-foreground">
                {t("code.task.baseWarning", { warning: card.baseWarning })}
              </p>
            ) : null}
            {card.status === "failed" ? (
              <p role="alert" className="mt-2 text-xs text-bad-foreground">
                {t("code.task.failed", { error: card.error ?? "" })}
              </p>
            ) : null}
            {actionable ? (
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  type="button"
                  className={BUTTON}
                  disabled={starting}
                  onClick={() => onStart(card, opts)}
                >
                  <Play className="h-3 w-3" aria-hidden />
                  {t("code.task.start")}
                </button>
                <button
                  type="button"
                  className={BUTTON}
                  disabled={starting || busy}
                  onClick={() => onHere(card)}
                >
                  <CornerDownLeft className="h-3 w-3" aria-hidden />
                  {t("code.task.here")}
                </button>
                <button
                  type="button"
                  className={BUTTON}
                  aria-expanded={open}
                  onClick={() => setOpenId(open ? null : card.id)}
                >
                  <SlidersHorizontal className="h-3 w-3" aria-hidden />
                  {t("code.task.options")}
                </button>
                <button type="button" className={BUTTON} disabled={starting} onClick={() => onDismiss(card)}>
                  <X className="h-3 w-3" aria-hidden />
                  {t("code.task.dismiss")}
                </button>
              </div>
            ) : null}
            {busy && actionable ? (
              <p className="mt-1 text-xs text-muted-foreground">{t("code.task.waitTurn")}</p>
            ) : null}
            {open && actionable ? (
              <div className="mt-2 flex flex-col gap-2 text-xs" data-testid="task-suggestion-options">
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={opts.newWorktree}
                    onChange={(e) =>
                      setOptions((prev) => ({ ...prev, [card.id]: { ...opts, newWorktree: e.target.checked } }))
                    }
                  />
                  {t("code.task.worktree")}
                </label>
                <label className="flex items-center gap-2">
                  {t("code.task.model")}
                  <input
                    type="text"
                    value={opts.model}
                    placeholder={t("code.task.modelDefault")}
                    maxLength={200}
                    className="min-w-0 flex-1 rounded-chip border border-hairline bg-surface-2 px-2 py-1"
                    onChange={(e) =>
                      setOptions((prev) => ({ ...prev, [card.id]: { ...opts, model: e.target.value } }))
                    }
                  />
                </label>
                <p className="text-muted-foreground">{t("code.task.inheritsProject")}</p>
              </div>
            ) : null}
          </article>
        );
      })}
    </section>
  );
}
