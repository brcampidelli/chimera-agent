import { Fragment, useCallback, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";

export interface TabItem<T extends string> {
  value: T;
  label: string;
  /** Optional trailing count — "Skills 12". Omit rather than pass 0. */
  count?: number;
  /**
   * A heading shown above the first tab of a run that shares it — "Personalization" over Skills,
   * Connectors and Plugins. Purely visual: it is not a tab, the arrow keys never land on it, and the
   * strip stays one tab stop. Tabs without one sit above the first group, ungrouped.
   */
  group?: string;
}

/**
 * A tab strip.
 *
 * Hand-built rather than pulled in: the whole of it is a roving tabindex and the right ARIA, and
 * both are short enough to read in one screen. The roving part is the bit people skip — a tab strip
 * where every tab is a tab stop makes a keyboard user press Tab five times to leave the group.
 * Here the strip is one stop and the arrow keys move within it, which is the expected behaviour.
 */
export function Tabs<T extends string>({
  items,
  value,
  onChange,
  className,
  orientation = "horizontal",
  "aria-label": ariaLabel,
}: {
  items: readonly TabItem<T>[];
  value: T;
  onChange: (next: T) => void;
  className?: string;
  /** Vertical is a column from `lg` up and the usual strip below it, for a screen whose sections
   *  outnumber what a strip shows comfortably. Either arrow axis moves the selection then, since
   *  which one is "along the strip" depends on the window's width. */
  orientation?: "horizontal" | "vertical";
  "aria-label": string;
}) {
  const vertical = orientation === "vertical";
  const id = useId();
  const refs = useRef(new Map<T, HTMLButtonElement>());

  const onKeyDown = useCallback(
    (e: KeyboardEvent<HTMLDivElement>) => {
      const keys = vertical
        ? ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"]
        : ["ArrowLeft", "ArrowRight", "Home", "End"];
      if (!keys.includes(e.key)) return;
      e.preventDefault();
      const i = items.findIndex((t) => t.value === value);
      const next =
        e.key === "Home"
          ? 0
          : e.key === "End"
            ? items.length - 1
            : // Wraps: arrowing past the last tab returns to the first, which is what the pattern
              // specifies and what people try.
              (i + (e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      const target = items[next];
      onChange(target.value);
      // Selection follows focus here, so move focus too — otherwise the arrow key changes the panel
      // while the ring stays behind on the old tab.
      refs.current.get(target.value)?.focus();
    },
    [items, value, onChange, vertical],
  );

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation={vertical ? "vertical" : undefined}
      onKeyDown={onKeyDown}
      className={cn(
        "flex items-center gap-1 border-b border-hairline",
        vertical && "lg:flex-col lg:items-stretch lg:gap-0.5 lg:border-b-0 lg:border-r",
        className,
      )}
    >
      {items.map((tab, index) => {
        const selected = tab.value === value;
        // A heading where the group changes, never between two tabs of the same one. Rendered as a
        // sibling of the tabs but `role="presentation"`, so the tablist still owns only tabs and a
        // screen reader counts "tab 3 of 9" across the whole strip rather than per group.
        const heading =
          tab.group !== undefined && tab.group !== items[index - 1]?.group ? tab.group : null;
        return (
          <Fragment key={tab.value}>
          {heading !== null && (
            <div
              role="presentation"
              className={cn(
                "px-3 text-xs font-semibold uppercase text-muted-foreground",
                // In a column it heads the run below it; in a strip (and the vertical strip below
                // `lg`) the tabs already read as one row, and a label between them would only push
                // them apart.
                vertical ? "hidden lg:block lg:pb-1 lg:pt-4" : "hidden",
              )}
            >
              {heading}
            </div>
          )}
          <button
            ref={(el) => {
              if (el) refs.current.set(tab.value, el);
              else refs.current.delete(tab.value);
            }}
            role="tab"
            id={`${id}-tab-${tab.value}`}
            aria-selected={selected}
            aria-controls={`${id}-panel-${tab.value}`}
            // The roving tabindex: exactly one tab is reachable by Tab.
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.value)}
            className={cn(
              "relative -mb-px px-3 py-2 text-sm transition-colors duration-1 ease-out",
              vertical && "lg:-mb-0 lg:-mr-px lg:rounded-l-md lg:text-left",
              focusRing,
              selected
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
            {tab.count !== undefined && (
              <span className="ml-1.5 text-xs text-muted-foreground">{tab.count}</span>
            )}
            {selected && (
              <span
                aria-hidden
                className={cn(
                  "absolute inset-x-0 bottom-0 h-px bg-accent-grad",
                  vertical && "lg:inset-x-auto lg:inset-y-1 lg:right-0 lg:h-auto lg:w-0.5",
                )}
              />
            )}
          </button>
          </Fragment>
        );
      })}
    </div>
  );
}

/** The panel a tab controls. Pair the `id` with the `Tabs` above it via the same `tabsId`. */
export function TabPanel({
  tabsId,
  value,
  children,
}: {
  tabsId: string;
  value: string;
  children: ReactNode;
}) {
  return (
    <div
      role="tabpanel"
      id={`${tabsId}-panel-${value}`}
      aria-labelledby={`${tabsId}-tab-${value}`}
      // Focusable so that tabbing out of the strip lands in the content it controls, rather than
      // skipping over a panel that has no interactive elements of its own.
      tabIndex={0}
      className="min-h-0 flex-1 focus-visible:outline-hidden"
    >
      {children}
    </div>
  );
}
