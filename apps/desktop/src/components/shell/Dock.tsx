import type { ReactNode } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useDndContext,
  useDroppable,
  useSensor,
  useSensors,
  type Announcements,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  horizontalListSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ArrowLeftRight, ChevronDown, GripVertical, Minus, X } from "lucide-react";

import { MaximizeButton } from "@/components/shell/Maximize";
import { DOCK_PANELS, isDockPanel, panelTitleKey, type MovablePanel } from "@/components/shell/panels";
import { focusRing } from "@/components/ui/focus";
import { useOptionalToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { useFloat } from "@/lib/float/host";
import { useT, type TFunc } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { PANELS, panelsIn, type Layout, type PanelId, type Zone } from "@/lib/layout/model";
import { cn } from "@/lib/utils";

/**
 * Panels that can be dragged between the side regions and the bottom dock (dynamic screen, phase 4).
 *
 * `@dnd-kit` is the one dependency the plan approved for this, and what it buys is the accessible half
 * of dragging: pick a panel up from its handle with Space or Enter, move it with the arrow keys (between
 * zones too), drop it with Space, cancel with Escape, and every step announced to a screen reader in the
 * reader's language. Pointer dragging needs a small movement first, so a click on the handle stays a
 * click. Every panel also has a "Move to…" menu, the path that needs neither.
 *
 * Where a panel is and in what order is the layout's, changed by one `move` action per drop; the layout
 * shows a hidden side region when something lands in it, so nothing is dropped out of sight.
 */
type DockZone = Extract<Zone, "left" | "right" | "bottom">;

const ZONES: readonly DockZone[] = ["left", "right", "bottom"];

function zoneOf(id: string | number, layout: Layout): DockZone | null {
  const key = String(id);
  if (key.startsWith("zone:")) return key.slice(5) as DockZone;
  if (!isDockPanel(key)) return null;
  const zone = layout.panels[key].zone;
  return zone === "left" || zone === "right" || zone === "bottom" ? zone : null;
}

/** Where a drop over `over` puts a panel: before a panel it lands on, at the end of an empty zone. */
export function dropTarget(layout: Layout, overId: string | number): { zone: DockZone; index: number } | null {
  const zone = zoneOf(overId, layout);
  if (!zone) return null;
  const ids = panelsIn(layout, zone);
  const key = String(overId);
  return { zone, index: key.startsWith("zone:") ? ids.length : Math.max(0, ids.indexOf(key as PanelId)) };
}

/** Exported for its test: what a screen reader hears at each step of a drag. */
export function announcements(t: TFunc, layout: Layout): Announcements {
  const name = (id: string | number) =>
    isDockPanel(String(id)) ? t(panelTitleKey(String(id) as MovablePanel)) : String(id);
  const where = (id: string | number | undefined) => {
    const zone = id === undefined ? null : zoneOf(id, layout);
    return zone ? t(`layout.region.${zone}`) : "";
  };
  return {
    onDragStart: ({ active }) => t("layout.dnd.start", { name: name(active.id) }),
    onDragOver: ({ active, over }) => (over ? t("layout.dnd.over", { name: name(active.id), zone: where(over.id) }) : undefined),
    onDragEnd: ({ active, over }) =>
      over ? t("layout.dnd.end", { name: name(active.id), zone: where(over.id) }) : t("layout.dnd.cancel", { name: name(active.id) }),
    onDragCancel: ({ active }) => t("layout.dnd.cancel", { name: name(active.id) }),
  };
}

/** The drag-and-drop context for every dock on the screen. Wraps the shell, so the Code screen's left
 *  dock and the shell's right and bottom ones are one space to drag in. */
export function LayoutDnd({ children }: { children: ReactNode }) {
  const t = useT();
  const { layout, dispatch } = useLayout();
  const sensors = useSensors(
    // A few pixels before a drag starts: a click on the handle is still a click.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  function onDragEnd({ active, over }: DragEndEvent) {
    if (!over || over.id === active.id || !isDockPanel(String(active.id))) return;
    const target = dropTarget(layout, over.id);
    if (target) dispatch({ type: "move", panel: String(active.id) as PanelId, ...target });
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragEnd={onDragEnd}
      accessibility={{
        announcements: announcements(t, layout),
        screenReaderInstructions: { draggable: t("layout.dnd.instructions") },
      }}
    >
      {children}
    </DndContext>
  );
}

/** One zone's panels. Renders nothing when it has none, except while something is being dragged, when
 *  an empty zone shows where a panel can land. */
export function Dock({ zone, className }: { zone: DockZone; className?: string }) {
  const t = useT();
  const { layout } = useLayout();
  const { active } = useDndContext();
  const { floating } = useFloat();
  // The maximised panel is drawn over the main area instead (phase 5), and a floating one in its own
  // window (phase 7): each is drawn once.
  const ids = panelsIn(layout, zone)
    .filter(isDockPanel)
    .filter((id) => id !== layout.maximized && !floating.has(id));
  const { setNodeRef, isOver } = useDroppable({ id: `zone:${zone}` });
  if (ids.length === 0 && !active) return null;

  return (
    <div
      ref={setNodeRef}
      data-zone={zone}
      aria-label={t(`layout.region.${zone}`)}
      role="region"
      className={cn(
        "flex min-h-0",
        zone === "bottom" ? "flex-row overflow-x-auto" : "flex-col",
        isOver && "bg-accent/10",
        className,
      )}
    >
      <SortableContext
        items={ids}
        strategy={zone === "bottom" ? horizontalListSortingStrategy : verticalListSortingStrategy}
      >
        {ids.map((id) => (
          <PanelFrame key={id} id={id} zone={zone} />
        ))}
      </SortableContext>
      {ids.length === 0 ? (
        <p className="m-auto rounded-chip border border-dashed border-hairline px-3 py-2 text-xs text-muted-foreground">
          {t("layout.dropHere")}
        </p>
      ) : null}
    </div>
  );
}

const iconButton = cn(
  "rounded-sm p-0.5 text-muted-foreground transition-colors duration-1 ease-out",
  "hover:bg-surface-hover hover:text-foreground",
  focusRing,
);

function PanelFrame({ id, zone }: { id: MovablePanel; zone: DockZone }) {
  const t = useT();
  const toast = useOptionalToast();
  const { layout, dispatch } = useLayout();
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id });
  const name = t(panelTitleKey(id));
  const minimized = layout.panels[id].mode === "minimized";

  function close() {
    if (!dispatch({ type: "set-mode", panel: id, mode: "closed" })) return;
    toast(t("layout.card.closed", { name }), "info", {
      label: t("layout.undo"),
      run: () => void dispatch({ type: "set-mode", panel: id, mode: "open" }),
    });
  }

  return (
    <section
      ref={setNodeRef}
      aria-label={name}
      data-panel={id}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "border-hairline bg-card/40",
        zone === "bottom" ? "w-72 shrink-0 border-r" : "border-t",
        // A panel whose content has nothing to say (Fusion without a fused turn, no background jobs)
        // hides its frame too, so a moved panel never leaves an empty box behind.
        "has-[>[data-panel-body]:empty]:hidden",
        isDragging && "relative z-10 opacity-70",
      )}
    >
      <div className="flex items-center gap-1 px-2 py-1.5">
        <Tooltip label={t("layout.dragHandle", { name })}>
          <button
            type="button"
            ref={setActivatorNodeRef}
            {...attributes}
            {...listeners}
            aria-label={t("layout.dragHandle", { name })}
            className={cn(iconButton, "cursor-grab touch-none")}
          >
            <GripVertical className="h-3.5 w-3.5" />
          </button>
        </Tooltip>
        <span className="min-w-0 flex-1 truncate text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {name}
        </span>
        <MoveMenu id={id} zone={zone} name={name} />
        {PANELS[id].maximizable ? <MaximizeButton panel={id} name={name} /> : null}
        <Tooltip label={t(minimized ? "layout.card.expand" : "layout.card.minimize", { name })}>
          <button
            type="button"
            aria-label={t(minimized ? "layout.card.expand" : "layout.card.minimize", { name })}
            aria-expanded={!minimized}
            onClick={() => dispatch({ type: "set-mode", panel: id, mode: minimized ? "open" : "minimized" })}
            className={iconButton}
          >
            {minimized ? <ChevronDown className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
          </button>
        </Tooltip>
        <Tooltip label={t("layout.card.close", { name })}>
          <button type="button" aria-label={t("layout.card.close", { name })} onClick={close} className={iconButton}>
            <X className="h-3.5 w-3.5" />
          </button>
        </Tooltip>
      </div>
      {minimized ? null : (
        <div data-panel-body className="px-4 pb-3">
          {DOCK_PANELS[id]()}
        </div>
      )}
    </section>
  );
}

/** The path that needs neither a pointer nor the drag keys: pick where the panel goes. */
function MoveMenu({ id, zone, name }: { id: MovablePanel; zone: DockZone; name: string }) {
  const t = useT();
  const toast = useOptionalToast();
  const { layout, dispatch } = useLayout();
  const { canFloat, popOut } = useFloat();
  const item = cn(
    "flex cursor-default items-center rounded-md px-2 py-1 text-xs outline-hidden",
    "data-highlighted:bg-surface-hover",
  );
  return (
    <Menu.Root>
      <Tooltip label={t("layout.moveTo", { name })}>
        <Menu.Trigger aria-label={t("layout.moveTo", { name })} className={iconButton}>
          <ArrowLeftRight className="h-3.5 w-3.5" />
        </Menu.Trigger>
      </Tooltip>
      <Menu.Portal>
        <Menu.Content align="end" sideOffset={4} className="overlay floating z-50 min-w-48 p-1 text-foreground">
          {ZONES.filter((z) => z !== zone).map((z) => (
            <Menu.Item
              key={z}
              className={item}
              onSelect={() => dispatch({ type: "move", panel: id, zone: z, index: panelsIn(layout, z).length })}
            >
              {t(`layout.moveTo.${z}`)}
            </Menu.Item>
          ))}
          {canFloat ? (
            <>
              <Menu.Separator className="my-1 h-px bg-hairline" />
              <Menu.Item
                className={item}
                onSelect={() => {
                  // A refused window leaves the panel where it was, and says so rather than doing nothing.
                  if (!popOut(id)) toast(t("layout.float.refused", { name }), "bad");
                }}
              >
                {t("layout.moveTo.window")}
              </Menu.Item>
            </>
          ) : null}
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
