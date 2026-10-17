import { useMemo, useState, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";
import { definitionSourceLabel } from "@/lib/definition-source";
import type { TFunc } from "@/lib/i18n";
import type { PromptCommand } from "@/lib/types";

/**
 * The composer's `/` list: the Markdown commands this conversation's folder defines.
 *
 * They worked in the terminal and nowhere in the app, so a command written in a repository was a
 * file only the terminal could read. Picking one inserts `/name ` and nothing more — the server
 * expands it when the turn is sent, by the terminal's rules, and the receipt under the answer says
 * which command it became. Nothing runs from this list.
 *
 * Open while the draft is a bare `/word` (no space yet, so it is still the name being typed), and
 * only when something matches: an Enter with nothing to pick must still send the message.
 */

/** The query a draft is typing, or null when the draft is not a command being named. */
export function slashQuery(draft: string): string | null {
  const match = /^\/(\S*)$/.exec(draft);
  return match ? match[1] : null;
}

/** Name-prefix matches first, then names that contain the query, each in the server's order. */
export function matchCommands(commands: readonly PromptCommand[], query: string): PromptCommand[] {
  const q = query.toLowerCase();
  const starts = commands.filter((c) => c.name.toLowerCase().startsWith(q));
  const contains = commands.filter(
    (c) => !c.name.toLowerCase().startsWith(q) && c.name.toLowerCase().includes(q),
  );
  return [...starts, ...contains];
}

export interface SlashMenuState {
  open: boolean;
  matches: PromptCommand[];
  active: number;
  setActive: (i: number) => void;
  /** Handle a key while the menu is open; true when it was the menu's key and must not reach the box. */
  onKeyDown: (ev: KeyboardEvent<HTMLTextAreaElement>) => boolean;
  pick: (command: PromptCommand) => void;
}

export function useSlashMenu(
  draft: string,
  commands: readonly PromptCommand[] | undefined,
  setDraft: (text: string) => void,
): SlashMenuState {
  const [active, setActive] = useState(0);
  // Escape closes the list for the draft it was pressed on; typing anything else opens it again.
  const [dismissed, setDismissed] = useState<string | null>(null);
  const query = slashQuery(draft);
  const matches = useMemo(
    () => (query === null || !commands ? [] : matchCommands(commands, query)),
    [commands, query],
  );
  const open = matches.length > 0 && dismissed !== draft;
  const current = Math.min(active, Math.max(matches.length - 1, 0));

  const pick = (command: PromptCommand) => {
    setDraft(`/${command.name} `);
    setActive(0);
  };

  const onKeyDown = (ev: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!open) return false;
    if (ev.key === "ArrowDown") {
      setActive((current + 1) % matches.length);
    } else if (ev.key === "ArrowUp") {
      setActive((current - 1 + matches.length) % matches.length);
    } else if ((ev.key === "Enter" && !ev.shiftKey) || ev.key === "Tab") {
      pick(matches[current]);
    } else if (ev.key === "Escape") {
      setDismissed(draft);
    } else {
      return false;
    }
    ev.preventDefault();
    return true;
  };

  return { open, matches, active: current, setActive, onKeyDown, pick };
}

export function SlashMenu({
  id,
  state,
  t,
}: {
  id: string;
  state: SlashMenuState;
  t: TFunc;
}) {
  if (!state.open) return null;
  return (
    <ul
      id={id}
      role="listbox"
      aria-label={t("composer.slash.title")}
      className="floating mb-1.5 max-h-60 overflow-y-auto p-1.5"
    >
      {state.matches.map((command, i) => (
        <li
          key={command.name}
          id={`${id}-${i}`}
          role="option"
          aria-selected={i === state.active}
          onMouseEnter={() => state.setActive(i)}
          // mousedown, not click: the box would lose focus first and the next key would go nowhere.
          onMouseDown={(ev) => {
            ev.preventDefault();
            state.pick(command);
          }}
          className={cn(
            "flex cursor-default flex-col gap-0.5 rounded-chip px-2.5 py-1.5 text-sm",
            i === state.active && "bg-surface-hover",
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate font-mono">/{command.name}</span>
            {command.argument_hint ? (
              <span className="truncate font-mono text-xs text-muted-foreground">
                {command.argument_hint}
              </span>
            ) : null}
            <span className="ml-auto shrink-0 text-xs text-muted-foreground">
              {definitionSourceLabel(t, command.source)}
            </span>
          </span>
          {command.error ? (
            <span className="text-xs text-bad-foreground">
              {t("composer.slash.broken", { error: command.error })}
            </span>
          ) : command.description ? (
            <span className="truncate text-xs text-muted-foreground">{command.description}</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
