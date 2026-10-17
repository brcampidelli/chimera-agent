import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check, Search, SlidersHorizontal } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type StateFilter = "all" | "blocked" | "review";

/** Search, the list's one setting, and the state filters — the sidebar's second and third lines.
 *
 * Search runs on the client over what is already listed (title, branch, project name): the list is
 * small and here, and a round trip per keystroke would buy nothing. The setting lives behind one
 * button rather than in Settings because it is about how THIS list reads, and the person deciding
 * is looking at the list.
 */
export function SidebarToolbar({
  query,
  onQuery,
  groupWorktrees,
  onGroupWorktrees,
  filter,
  onFilter,
  blockedCount,
  reviewCount,
}: {
  query: string;
  onQuery: (next: string) => void;
  groupWorktrees: boolean;
  onGroupWorktrees: (on: boolean) => void;
  filter: StateFilter;
  onFilter: (next: StateFilter) => void;
  blockedCount: number;
  reviewCount: number;
}) {
  const t = useT();
  return (
    <>
      <div className="flex items-center gap-1 px-2 pb-2">
        <label className="field flex h-7 min-w-0 flex-1 items-center gap-1.5 px-2">
          <Search aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <input
            type="search"
            className="min-w-0 flex-1 bg-transparent text-xs outline-hidden placeholder:text-muted-foreground"
            placeholder={t("code.sessions.search")}
            aria-label={t("code.sessions.search")}
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape" && query) {
                e.stopPropagation();
                onQuery("");
              }
            }}
          />
        </label>
        <Menu.Root>
          <Menu.Trigger
            aria-label={t("code.sessions.viewOptions")}
            title={t("code.sessions.viewOptions")}
            className={cn(
              "rounded-md p-1.5 text-muted-foreground transition-colors duration-1 ease-out hover:text-foreground",
              focusRing,
            )}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Content align="end" sideOffset={4} className="overlay floating z-50 min-w-56 p-1 text-foreground">
              <Menu.CheckboxItem
                checked={groupWorktrees}
                onCheckedChange={(on) => onGroupWorktrees(on === true)}
                className="flex cursor-default items-center gap-2 rounded-md px-2 py-1 text-xs outline-hidden data-highlighted:bg-surface-hover"
              >
                <span className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center">
                  <Menu.ItemIndicator>
                    <Check className="h-3.5 w-3.5" aria-hidden />
                  </Menu.ItemIndicator>
                </span>
                {t("code.sessions.groupWorktrees")}
              </Menu.CheckboxItem>
            </Menu.Content>
          </Menu.Portal>
        </Menu.Root>
      </div>
      {/* Only when there is something to filter to: a filter of nothing is two dead buttons. */}
      {blockedCount > 0 || reviewCount > 0 || filter !== "all" ? (
        <div className="px-2 pb-2">
          <div
            role="group"
            aria-label={t("code.sessions.filters")}
            // Pills that wrap rather than one bordered strip: at 240px the three labels do not fit on
            // one line in most languages, and a strip broken over two lines reads as two controls.
            className="flex flex-wrap gap-1"
          >
            {([
              ["all", "code.sessions.filterAll", 0],
              ["blocked", "code.sessions.filterWaiting", blockedCount],
              ["review", "code.sessions.filterReview", reviewCount],
            ] as const).map(([value, key, count]) => (
              <button
                key={value}
                type="button"
                aria-pressed={filter === value}
                onClick={() => onFilter(value)}
                className={cn(
                  "rounded-full border px-2 py-0.5 text-xs transition-colors duration-1 ease-out",
                  focusRing,
                  filter === value
                    ? "border-accent/40 bg-accent/15 text-accent-ink"
                    : "border-hairline text-muted-foreground hover:text-foreground",
                )}
              >
                {t(key, { n: count })}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </>
  );
}
