import { useId, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode, type RefObject } from "react";
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
import {
  DOCK_PANELS,
  PANEL_SUMMARIES,
  canFloatPanel,
  isDockPanel,
  panelShortTitleKey,
  panelTitleKey,
  useSilentPanels,
  type MovablePanel,
} from "@/components/shell/panels";
import { focusRing } from "@/components/ui/focus";
import { useOptionalToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { useFloat } from "@/lib/float/host";
import { useT, type TFunc } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import {
  PANELS,
  SPLIT_LIMITS,
  canLandIn,
  panelsIn,
  tabGroupOf,
  type Layout,
  type PanelId,
  type PanelSplit,
  type Zone,
} from "@/lib/layout/model";
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

/** Where a panel frame can be drawn: a dock, or the main column for a panel that lives there (the
 *  batch board, `AgentBatchSlot`). */
export type FrameZone = DockZone | "center";

const ZONES: readonly DockZone[] = ["left", "right", "bottom"];

function zoneOf(id: string | number, layout: Layout): FrameZone | null {
  const key = String(id);
  if (key.startsWith("zone:")) return key.slice(5) as FrameZone;
  if (!isDockPanel(key)) return null;
  const zone = layout.panels[key].zone;
  return zone === "left" || zone === "right" || zone === "bottom" || zone === "center" ? zone : null;
}

/** Where a drop over `over` puts a panel: before a panel it lands on, at the end of an empty zone. */
export function dropTarget(layout: Layout, overId: string | number): { zone: FrameZone; index: number } | null {
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
    if (!zone) return "";
    return zone === "center" ? t("layout.moveTo.center") : t(`layout.region.${zone}`);
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
    if (target && canLandIn(String(active.id) as PanelId, target.zone)) {
      dispatch({ type: "move", panel: String(active.id) as PanelId, ...target });
    }
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

/**
 * What a dock draws, in order: a lone panel, or a tab group (panels changed C9 into tabs of one frame).
 *
 * A group with only one member on screen (the others closed, maximised or floating) draws as that lone
 * panel, so no strip of a single tab is ever left. `key` is the panel the unit drags as: a group drags
 * as its selected tab, and a drop moves that panel alone.
 */
export interface DockUnit {
  key: MovablePanel;
  /** The group's members on screen, in the dock's order; only `key` for a lone panel. */
  members: MovablePanel[];
  /** The group's first member, which names it in `activeTabs`; null for a lone panel. */
  anchor: PanelId | null;
}

export function dockUnits(layout: Layout, ids: readonly MovablePanel[]): DockUnit[] {
  const units: DockUnit[] = [];
  const seen = new Set<PanelId>();
  for (const id of ids) {
    if (seen.has(id)) continue;
    const group = tabGroupOf(layout, id);
    const members = group ? ids.filter((m) => group.includes(m)) : [id];
    members.forEach((m) => seen.add(m));
    if (!group || members.length < 2) {
      units.push({ key: id, members: [id], anchor: null });
      continue;
    }
    const chosen = layout.activeTabs[group[0]];
    const key = members.find((m) => m === chosen) ?? members[0];
    units.push({ key, members, anchor: group[0] });
  }
  return units;
}

/** The units of each half of a split: the second half holds what the split names, the first the rest. */
export function splitUnits(units: readonly DockUnit[], split: PanelSplit): [DockUnit[], DockUnit[]] {
  const inSecond = (u: DockUnit) => u.members.some((m) => split.second.includes(m));
  return [units.filter((u) => !inSecond(u)), units.filter(inSecond)];
}

/** The panels a dock draws, in order: on screen in the layout, not maximised, not floating, and not
 *  silent (`useSilentPanels`). Shared by the dock and the "Group with" menu, so the menu never offers a
 *  panel the person cannot see. */
function onScreenIn(
  layout: Layout,
  zone: FrameZone,
  floating: ReadonlySet<MovablePanel>,
  silent: ReadonlySet<MovablePanel>,
): MovablePanel[] {
  return panelsIn(layout, zone)
    .filter(isDockPanel)
    .filter((id) => id !== layout.maximized && !floating.has(id) && !silent.has(id));
}

/** One zone's panels. Renders nothing when it has none, except while something is being dragged, when
 *  an empty zone shows where a panel can land. A split zone draws its two halves with a divider between
 *  them; a half left with nothing in it gives the whole dock to the other. */
export function Dock({ zone, className }: { zone: DockZone; className?: string }) {
  const t = useT();
  const { layout } = useLayout();
  const { active } = useDndContext();
  const { floating } = useFloat();
  const silent = useSilentPanels();
  const container = useRef<HTMLDivElement>(null);
  // The maximised panel is drawn over the main area instead (phase 5), and a floating one in its own
  // window (phase 7): each is drawn once. A panel with nothing to say is not drawn at all, frame
  // included, and comes back with its first data; inside a tab group it drops out of the strip.
  const ids = onScreenIn(layout, zone, floating, silent);
  const { setNodeRef, isOver } = useDroppable({ id: `zone:${zone}` });
  if (ids.length === 0 && !active) return null;

  const units = dockUnits(layout, ids);
  const split = layout.splits[zone];
  const halves = split ? splitUnits(units, split) : null;
  const divided = split && halves && halves[0].length > 0 && halves[1].length > 0 ? { split, halves } : null;
  const row = zone === "bottom";
  const strategy = row ? horizontalListSortingStrategy : verticalListSortingStrategy;
  const drawn = (list: DockUnit[]) => (
    <SortableContext items={list.map((u) => u.key)} strategy={strategy}>
      {list.map((unit) => (
        <PanelFrame key={unit.anchor ?? unit.key} unit={unit} zone={zone} />
      ))}
    </SortableContext>
  );

  return (
    <div
      ref={(node) => {
        setNodeRef(node);
        container.current = node;
      }}
      data-zone={zone}
      aria-label={t(`layout.region.${zone}`)}
      role="region"
      className={cn(
        "flex min-h-0",
        divided
          ? cn("flex-1", divided.split.orientation === "horizontal" ? "flex-row" : "flex-col")
          : row
            ? "flex-row overflow-x-auto"
            : "flex-col",
        isOver && "bg-accent/10",
        className,
      )}
    >
      {divided ? (
        <>
          <SplitHalf grow={divided.split.ratio} row={row} half="first">
            {drawn(divided.halves[0])}
          </SplitHalf>
          <SplitDivider zone={zone} split={divided.split} container={container} />
          <SplitHalf grow={1 - divided.split.ratio} row={row} half="second">
            {drawn(divided.halves[1])}
          </SplitHalf>
        </>
      ) : (
        drawn(units)
      )}
      {ids.length === 0 ? (
        <p className="m-auto rounded-chip border border-dashed border-hairline px-3 py-2 text-xs text-muted-foreground">
          {t("layout.dropHere")}
        </p>
      ) : null}
    </div>
  );
}

function SplitHalf({ grow, row, half, children }: { grow: number; row: boolean; half: "first" | "second"; children: ReactNode }) {
  return (
    <div
      data-split-half={half}
      // The share is the person's, not a design value: a flex-grow ratio, the way the dock's widths
      // are pixels the layout keeps rather than tokens.
      style={{ flexGrow: grow }}
      className={cn("flex min-h-0 min-w-0 basis-0 overflow-auto", row ? "flex-row" : "flex-col")}
    >
      {children}
    </div>
  );
}

/** Keyboard step of the divider, as a share of the dock. */
const SPLIT_STEP = 0.05;

/**
 * The line between the two halves of a split dock: the window-splitter pattern `Splitter` uses for
 * the columns, measured as a share of the dock. Arrow keys move it, Home centres it, and so does a
 * double click; dragging follows the pointer. The layout clamps it, so neither half can vanish.
 */
function SplitDivider({ zone, split, container }: { zone: DockZone; split: PanelSplit; container: RefObject<HTMLDivElement | null> }) {
  const t = useT();
  const { dispatch, settle } = useLayout();
  const dragging = useRef(false);
  const sideBySide = split.orientation === "horizontal";
  const resize = (ratio: number) => void dispatch({ type: "resize-split", zone, ratio });

  function fromPointer(e: PointerEvent<HTMLDivElement>) {
    const box = container.current?.getBoundingClientRect();
    if (!box) return;
    const span = sideBySide ? box.width : box.height;
    if (span <= 0) return;
    resize(sideBySide ? (e.clientX - box.left) / span : (e.clientY - box.top) / span);
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const back = sideBySide ? "ArrowLeft" : "ArrowUp";
    const forward = sideBySide ? "ArrowRight" : "ArrowDown";
    if (e.key === back || e.key === forward) {
      e.preventDefault();
      resize(split.ratio + (e.key === forward ? SPLIT_STEP : -SPLIT_STEP));
    } else if (e.key === "Home") {
      e.preventDefault();
      settle();
      resize(SPLIT_LIMITS.initial);
    }
  }

  return (
    <div
      role="separator"
      // A side-by-side split has an upright line between the halves.
      aria-orientation={sideBySide ? "vertical" : "horizontal"}
      aria-label={t("layout.split.resize")}
      aria-valuenow={Math.round(split.ratio * 100)}
      aria-valuemin={Math.round(SPLIT_LIMITS.min * 100)}
      aria-valuemax={Math.round(SPLIT_LIMITS.max * 100)}
      tabIndex={0}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.preventDefault();
        settle();
        dragging.current = true;
        e.currentTarget.setPointerCapture?.(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (dragging.current) fromPointer(e);
      }}
      onPointerUp={(e) => {
        dragging.current = false;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      }}
      onPointerCancel={() => {
        dragging.current = false;
      }}
      onKeyDown={onKeyDown}
      onBlur={settle}
      onDoubleClick={() => {
        settle();
        resize(SPLIT_LIMITS.initial);
      }}
      className={cn(
        "group relative z-10 shrink-0 touch-none select-none",
        sideBySide ? "-mx-1 w-2 cursor-col-resize" : "-my-1 h-2 cursor-row-resize",
        focusRing,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute bg-hairline transition-colors duration-1 ease-out group-hover:bg-accent",
          sideBySide ? "inset-y-0 left-1/2 w-px -translate-x-1/2" : "inset-x-0 top-1/2 h-px -translate-y-1/2",
        )}
      />
    </div>
  );
}

const iconButton = cn(
  "rounded-sm p-0.5 text-muted-foreground transition-colors duration-1 ease-out",
  "hover:bg-surface-hover hover:text-foreground",
  focusRing,
);

/**
 * One panel's frame: its title, the drag handle, and move, maximise, minimise and close. Exported for
 * the batch board, which is drawn in the main column with this same frame (`AgentBatchSlot`) so it is
 * minimised, closed and moved by the controls every other panel has.
 *
 * `movable: false` drops the drag handle and the move menu, for a window with no docks to move to (a
 * conversation in a window of its own): moving there would put the panel somewhere nothing draws.
 */
export function PanelFrame({ unit, zone, movable = true }: { unit: DockUnit; zone: FrameZone; movable?: boolean }) {
  const t = useT();
  const toast = useOptionalToast();
  const { layout, dispatch } = useLayout();
  const id = unit.key;
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id, disabled: !movable });
  const summary = PANEL_SUMMARIES[id];
  const name = t(panelTitleKey(id));
  const shortName = t(panelShortTitleKey(id));
  const minimized = layout.panels[id].mode === "minimized";
  const tabsId = useId();
  const grouped = unit.anchor !== null && unit.members.length > 1;

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
      data-tab-group={grouped ? unit.anchor : undefined}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "group/panel border-hairline bg-card/40",
        zone === "bottom"
          ? "w-72 shrink-0 border-r"
          : zone === "center"
            ? // In the main column, under the conversation: at most half of it, scrolling inside.
              "flex max-h-1/2 min-h-0 shrink-0 flex-col border-t"
            : "border-t",
        // A panel whose content has nothing to say (Fusion without a fused turn, no background jobs)
        // hides its frame too, so a moved panel never leaves an empty box behind. Not a tab group: its
        // other tabs still have something to say, and hiding the frame would hide them with it.
        !grouped && "has-[>[data-panel-body]:empty]:hidden",
        isDragging && "relative z-10 opacity-70",
      )}
    >
      <div className="flex shrink-0 items-center gap-1 px-2 py-1.5">
        {movable ? (
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
        ) : null}
        {grouped && unit.anchor !== null ? (
          <TabStrip unit={unit} anchor={unit.anchor} idPrefix={tabsId} />
        ) : (
          // Sentence case, not uppercase with wide tracking: caps and tracking made each title 15 to 20%
          // wider and cut "Background jobs" short beside the buttons. The full name is the section's
          // accessible name; `title` shows it to a pointer when the short one is drawn.
          <span title={name} className="min-w-0 flex-1 truncate text-xs font-semibold text-muted-foreground">
            {shortName}
            {/* Where the panel is, beside its name: what a minimised panel still has to say. */}
            {summary ? (
              <span data-panel-summary className="font-normal">
                {" · "}
                {summary()}
              </span>
            ) : null}
          </span>
        )}
        {/* Move, maximise and minimise show while the panel is under the pointer or holds focus, and take
            no room otherwise, which is what gives the title its width back. Tab reaches them: the drag
            handle before them puts focus inside the panel, which shows them; going back, the close
            button after them does the same. They stay while the move menu is open, so the menu keeps
            its anchor and focus has somewhere to return. They appear at once, with no transition, so
            reduced motion has nothing to take away. */}
        <div
          data-panel-controls
          className="hidden items-center gap-1 group-hover/panel:flex group-focus-within/panel:flex has-[[data-menu-open]]:flex"
        >
          {movable ? <MoveMenu id={id} zone={zone} name={name} /> : null}
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
        </div>
        <Tooltip label={t("layout.card.close", { name })}>
          <button type="button" aria-label={t("layout.card.close", { name })} onClick={close} className={iconButton}>
            <X className="h-3.5 w-3.5" />
          </button>
        </Tooltip>
      </div>
      {grouped ? (
        // The panel of the selected tab, there even when minimised: `aria-controls` must point at
        // something. Switching tabs swaps its content at once; nothing animates, so reduced motion
        // has nothing to take away.
        <div
          role="tabpanel"
          id={`${tabsId}-panel`}
          aria-labelledby={`${tabsId}-tab-${id}`}
          className={minimized ? "hidden" : undefined}
        >
          {minimized ? null : (
            <div data-panel-body className="px-4 pb-3">
              {DOCK_PANELS[id]()}
            </div>
          )}
        </div>
      ) : minimized ? null : (
        <div data-panel-body className={cn("px-4 pb-3", zone === "center" && "min-h-0 overflow-y-auto")}>
          {DOCK_PANELS[id]()}
        </div>
      )}
    </section>
  );
}

/**
 * The tabs of a group: the WAI-ARIA tabs pattern with automatic activation. One tab is in the Tab
 * order (the selected one); the arrow keys move between tabs and select as they go, Home and End go
 * to the ends. No Ctrl+Tab: that belongs to the conversation (C4), and a panel taking it would steal
 * it whenever focus happened to sit here.
 */
function TabStrip({ unit, anchor, idPrefix }: { unit: DockUnit; anchor: PanelId; idPrefix: string }) {
  const t = useT();
  const { dispatch } = useLayout();
  const buttons = useRef(new Map<MovablePanel, HTMLButtonElement>());

  function select(panel: MovablePanel, focus: boolean) {
    dispatch({ type: "activate-tab", group: anchor, panel });
    if (focus) buttons.current.get(panel)?.focus();
  }

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const at = unit.members.indexOf(unit.key);
    const last = unit.members.length - 1;
    const next =
      e.key === "ArrowRight" ? (at === last ? 0 : at + 1)
      : e.key === "ArrowLeft" ? (at === 0 ? last : at - 1)
      : e.key === "Home" ? 0
      : e.key === "End" ? last
      : null;
    if (next === null) return;
    e.preventDefault();
    select(unit.members[next], true);
  }

  return (
    <div
      role="tablist"
      aria-label={t("layout.tabs.list")}
      aria-orientation="horizontal"
      onKeyDown={onKeyDown}
      className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto"
    >
      {unit.members.map((member) => {
        const selected = member === unit.key;
        const full = t(panelTitleKey(member));
        const short = t(panelShortTitleKey(member));
        return (
          <button
            key={member}
            ref={(node) => {
              if (node) buttons.current.set(member, node);
              else buttons.current.delete(member);
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${member}`}
            aria-selected={selected}
            aria-controls={selected ? `${idPrefix}-panel` : undefined}
            tabIndex={selected ? 0 : -1}
            aria-label={short === full ? undefined : full}
            title={short === full ? undefined : full}
            onClick={() => select(member, false)}
            className={cn(
              "shrink-0 truncate rounded-sm px-1.5 py-0.5 text-xs font-semibold",
              "transition-colors duration-1 ease-out hover:bg-surface-hover",
              selected ? "bg-surface-hover text-foreground" : "text-muted-foreground",
              focusRing,
            )}
          >
            {short}
          </button>
        );
      })}
    </div>
  );
}

/** The path that needs neither a pointer nor the drag keys: pick where the panel goes. */
function MoveMenu({ id, zone, name }: { id: MovablePanel; zone: FrameZone; name: string }) {
  const t = useT();
  const toast = useOptionalToast();
  const { layout, dispatch } = useLayout();
  const { canFloat, popOut, floating } = useFloat();
  const silent = useSilentPanels();
  const [open, setOpen] = useState(false);
  const group = tabGroupOf(layout, id);
  // What this panel can be grouped with: the other units on screen in its dock, one entry per unit.
  const onScreen = onScreenIn(layout, zone, floating, silent);
  const units = dockUnits(layout, onScreen);
  const others = units.filter((u) => !u.members.includes(id));
  // The main column is not a dock: nothing there splits.
  const dock: DockZone | null = zone === "center" ? null : zone;
  const split = dock ? layout.splits[dock] : undefined;
  const item = cn(
    "flex cursor-default items-center rounded-md px-2 py-1 text-xs outline-hidden",
    "data-highlighted:bg-surface-hover",
  );
  return (
    <Menu.Root open={open} onOpenChange={setOpen}>
      <Tooltip label={t("layout.moveTo", { name })}>
        <Menu.Trigger
          aria-label={t("layout.moveTo", { name })}
          data-menu-open={open ? "" : undefined}
          className={iconButton}
        >
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
          {/* Home, for a panel that lives in the main column (the batch board) and was moved out of it. */}
          {zone !== "center" && canLandIn(id, "center") ? (
            <Menu.Item
              className={item}
              onSelect={() => dispatch({ type: "move", panel: id, zone: "center", index: panelsIn(layout, "center").length })}
            >
              {t("layout.moveTo.center")}
            </Menu.Item>
          ) : null}
          {others.length > 0 || group ? <Menu.Separator className="my-1 h-px bg-hairline" /> : null}
          {others.map((other) => (
            <Menu.Item
              key={other.key}
              className={item}
              onSelect={() => dispatch({ type: "group-tabs", panels: [other.key, id] })}
            >
              {/* A group is named by all its tabs, so the entry does not change with which one is selected. */}
              {t("layout.tabs.groupWith", { name: other.members.map((m) => t(panelTitleKey(m))).join(", ") })}
            </Menu.Item>
          ))}
          {group ? (
            <Menu.Item className={item} onSelect={() => dispatch({ type: "ungroup-tab", panel: id })}>
              {t("layout.tabs.ungroup")}
            </Menu.Item>
          ) : null}
          {dock && (split || units.length > 1) ? <Menu.Separator className="my-1 h-px bg-hairline" /> : null}
          {dock === null ? null : split ? (
            <>
              <Menu.Item className={item} onSelect={() => dispatch({ type: "split-move", panel: id })}>
                {t("layout.split.moveOther")}
              </Menu.Item>
              <Menu.Item className={item} onSelect={() => dispatch({ type: "set-split", zone: dock, orientation: null })}>
                {t("layout.split.off")}
              </Menu.Item>
            </>
          ) : units.length > 1 ? (
            <>
              <Menu.Item
                className={item}
                onSelect={() => dispatch({ type: "set-split", zone: dock, orientation: "horizontal", panel: id })}
              >
                {t("layout.split.sideBySide")}
              </Menu.Item>
              <Menu.Item
                className={item}
                onSelect={() => dispatch({ type: "set-split", zone: dock, orientation: "vertical", panel: id })}
              >
                {t("layout.split.stacked")}
              </Menu.Item>
            </>
          ) : null}
          {canFloat && canFloatPanel(id) ? (
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
