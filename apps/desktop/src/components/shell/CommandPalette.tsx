import { useEffect, useMemo, useRef, useState } from "react";
import * as RadixDialog from "@radix-ui/react-dialog";
import { Search } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

export interface Command {
  id: string;
  label: string;
  /** Where this leads — the destination name, or the word "session". Shown on the right. */
  group?: string;
  run: () => void;
}

/**
 * Jump anywhere by typing.
 *
 * Not `cmdk`: the matching is a `.filter()` over a list the app already has, and Radix Dialog
 * supplies the modal shell — focus trap, Escape, scroll lock. What is left is a text input and a
 * highlighted list, which is not worth a dependency.
 *
 * With five rail destinations this carries the long tail: the tabs inside them, and every session
 * by title. A palette over a flat fifteen-item nav would have been much less useful than one over
 * a well-grouped five.
 */
export function CommandPalette({
  open,
  onOpenChange,
  commands,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  commands: Command[];
}) {
  const t = useT();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    // Substring, not fuzzy. Fuzzy matching earns its keep over hundreds of entries; over dozens it
    // mostly surprises people by ranking something they didn't mean above something they did.
    return commands.filter(
      (c) => c.label.toLowerCase().includes(q) || c.group?.toLowerCase().includes(q),
    );
  }, [commands, query]);

  // Reset between openings: reopening onto a stale query is disorienting, and a stale index can
  // point past the end of a shorter list.
  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => {
        const n = matches.length;
        if (n === 0) return 0;
        return (i + (e.key === "ArrowDown" ? 1 : -1) + n) % n;
      });
    } else if (e.key === "Enter") {
      e.preventDefault();
      const chosen = matches[active];
      if (chosen) {
        onOpenChange(false);
        chosen.run();
      }
    }
  }

  const listId = "command-palette-list";

  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="backdrop fixed inset-0 z-50 bg-scrim" />
        <RadixDialog.Content
          aria-label={t("palette.title")}
          className="overlay floating fixed left-1/2 top-24 z-50 w-[min(34rem,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden p-0"
        >
          <RadixDialog.Title className="sr-only">{t("palette.title")}</RadixDialog.Title>
          <RadixDialog.Description className="sr-only">{t("palette.hint")}</RadixDialog.Description>

          <div className="flex items-center gap-2 border-b border-hairline px-3.5 py-3">
            <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder={t("palette.placeholder")}
              // Full combobox semantics, because this is hand-built: without them a screen reader
              // announces a plain text field and never mentions the list underneath it.
              role="combobox"
              aria-expanded
              aria-controls={listId}
              aria-activedescendant={matches[active] ? `cmd-${matches[active].id}` : undefined}
              aria-autocomplete="list"
              aria-label={t("palette.placeholder")}
              className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>

          <ul id={listId} ref={listRef} role="listbox" aria-label={t("palette.title")} className="max-h-80 overflow-y-auto p-1.5">
            {matches.length === 0 && (
              <li className="px-2.5 py-6 text-center text-sm text-muted-foreground">
                {t("palette.noResults")}
              </li>
            )}
            {matches.map((c, i) => (
              <li
                key={c.id}
                id={`cmd-${c.id}`}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => {
                  onOpenChange(false);
                  c.run();
                }}
                className={cn(
                  "flex cursor-default items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-sm",
                  i === active && "bg-surface-hover",
                )}
              >
                <span className="truncate">{c.label}</span>
                {c.group && (
                  <span className="shrink-0 text-xs text-muted-foreground">{c.group}</span>
                )}
              </li>
            ))}
          </ul>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
