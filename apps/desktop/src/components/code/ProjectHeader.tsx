import * as Menu from "@radix-ui/react-dropdown-menu";
import { ChevronRight, Ellipsis, GitFork, Plus } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import type { SidebarGroup } from "@/lib/sidebar-groups";
import { cn } from "@/lib/utils";

/** A project action that another feature contributes to the ⋯ menu (cleaning finished worktrees,
 *  for one). Carries its own label, so a feature that is not built contributes nothing and leaves no
 *  dead entry behind. */
export interface ExtraProjectAction {
  id: string;
  label: string;
  onSelect: () => void;
}

const ITEM = cn(
  "flex cursor-default items-center gap-2 rounded-md px-2 py-1 text-xs outline-hidden",
  "data-highlighted:bg-surface-hover",
);

/** The head of one project section: fold it, start a conversation in it, or reach the rest.
 *
 * The whole name is the fold toggle, as in every list of this shape, so Enter and Space fold and
 * the focus stays on the toggle. Starting a conversation is the "+" beside it — in the project's own
 * folder, its main checkout, never in some task's worktree. Opening, renaming, hiding and deleting
 * live behind ⋯: none of them was removed, they moved, and the header has room for the name again.
 */
export function ProjectHeader({
  group,
  isOpen,
  collapsed,
  onToggle,
  onNew,
  onOpenProject,
  onRename,
  onHide,
  onDelete,
  extraActions = [],
}: {
  group: SidebarGroup;
  /** The project the screen has open: never folded, and drawn in the accent. */
  isOpen: boolean;
  collapsed: boolean;
  onToggle: () => void;
  onNew: () => void;
  onOpenProject: () => void;
  onRename: () => void;
  onHide: () => void;
  onDelete: () => void;
  extraActions?: readonly ExtraProjectAction[];
}) {
  const t = useT();
  const name = group.key ? group.label : t("code.sessions.defaultProject");
  // On hover or focus anywhere in the header, and while its menu is open (the focus is then in the
  // menu's portal, outside this element, and the trigger must not vanish under the open menu).
  const reveal = cn(
    "px-1 py-1 text-muted-foreground opacity-0 transition-opacity duration-1 ease-out hover:text-foreground",
    "group-hover/project:opacity-100 group-focus-within/project:opacity-100 aria-expanded:opacity-100",
    focusRing,
  );
  return (
    <div className="group/project flex items-center">
      <button
        type="button"
        aria-expanded={!collapsed}
        onClick={onToggle}
        title={group.key || t("code.sessions.defaultProject")}
        className={cn(
          "flex min-w-0 flex-1 items-center gap-1 px-2 py-1 text-left text-xs font-semibold transition-colors duration-1 ease-out",
          focusRing,
          isOpen ? "text-accent-ink" : "text-foreground/70 hover:text-foreground",
        )}
      >
        <ChevronRight
          aria-hidden
          className={cn("h-3.5 w-3.5 shrink-0 transition-transform duration-1 ease-out", !collapsed && "rotate-90")}
        />
        <span className="truncate">{name}</span>
        {group.hint ? <span className="truncate font-normal text-muted-foreground">{group.hint}</span> : null}
        {group.worktrees > 0 ? (
          <span
            title={t("code.projects.worktrees", { n: group.worktrees })}
            className="ml-1 inline-flex shrink-0 items-center gap-0.5 font-normal tabular-nums text-muted-foreground"
          >
            <GitFork aria-hidden className="h-3 w-3" />
            <span aria-hidden>{group.worktrees}</span>
            <span className="sr-only">{t("code.projects.worktrees", { n: group.worktrees })}</span>
          </span>
        ) : null}
      </button>
      <button
        type="button"
        aria-label={t("code.projects.newIn", { name })}
        className={reveal}
        onClick={onNew}
      >
        <Plus className="h-3.5 w-3.5" />
      </button>
      <Menu.Root>
        <Menu.Trigger aria-label={t("code.projects.menuOne", { name })} className={cn(reveal, "pr-2")}>
          <Ellipsis className="h-3.5 w-3.5" />
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content align="end" sideOffset={4} className="overlay floating z-50 min-w-48 p-1 text-foreground">
            <Menu.Item className={ITEM} onSelect={onOpenProject}>
              {t("code.projects.open")}
            </Menu.Item>
            {isOpen ? null : (
              <Menu.Item className={ITEM} onSelect={onToggle}>
                {t(collapsed ? "code.projects.expand" : "code.projects.collapse")}
              </Menu.Item>
            )}
            {/* Naming, hiding or deleting the no-project section would act on a hole: it is where
                conversations nobody filed land. */}
            {group.key ? (
              <>
                <Menu.Item className={ITEM} onSelect={onRename}>
                  {t("code.projects.renameOne", { name })}
                </Menu.Item>
                <Menu.Item className={ITEM} onSelect={onHide}>
                  {t("code.projects.hide")}
                </Menu.Item>
              </>
            ) : null}
            {extraActions.map((action) => (
              <Menu.Item key={action.id} className={ITEM} onSelect={action.onSelect}>
                {action.label}
              </Menu.Item>
            ))}
            {group.key ? (
              <>
                <Menu.Separator className="my-1 h-px bg-hairline" />
                <Menu.Item className={cn(ITEM, "text-bad-foreground")} onSelect={onDelete}>
                  {t("code.projects.deleteOne", { name })}
                </Menu.Item>
              </>
            ) : null}
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
    </div>
  );
}
