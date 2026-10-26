import {
  AlignCenterHorizontal, AlignCenterVertical, AlignEndHorizontal, AlignEndVertical, AlignStartHorizontal, AlignStartVertical,
  Check, Circle, Copy, Frame, Hand, Keyboard, MessageSquarePlus, Minus, Monitor, MousePointer2,
  PenLine, Play, Plus, Redo2, Square, StickyNote, Trash2, Type, Undo2, ZoomIn, ZoomOut, type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { DesignMenu, DesignMenuItem, DesignMenuSeparator } from "@/components/design/DesignMenu";
import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import type { AlignMode } from "@/lib/design/geometry";
import { isIndexPage, type DesignPage } from "@/lib/design/model";
import { DESIGN_SHORTCUTS, displayKeys, type DesignShortcutId } from "@/lib/design/shortcuts";
import { TOOL_SHORTCUT, type DesignCommand, type DesignTool, type DrawStyle } from "@/lib/design/tools";
import { useNum, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const TOOL_ICON: Record<DesignTool, LucideIcon> = {
  select: MousePointer2, pan: Hand, text: Type, frame: Frame, note: StickyNote, rectangle: Square,
  ellipse: Circle, line: Minus, draw: PenLine, comment: MessageSquarePlus,
};

const ALIGN: { mode: AlignMode; icon: LucideIcon; labelKey: string }[] = [
  { mode: "left", icon: AlignStartVertical, labelKey: "design.align.left" },
  { mode: "center", icon: AlignCenterVertical, labelKey: "design.align.center" },
  { mode: "right", icon: AlignEndVertical, labelKey: "design.align.right" },
  { mode: "top", icon: AlignStartHorizontal, labelKey: "design.align.top" },
  { mode: "middle", icon: AlignCenterHorizontal, labelKey: "design.align.middle" },
  { mode: "bottom", icon: AlignEndHorizontal, labelKey: "design.align.bottom" },
];

/** The keys a shortcut row shows, read from the same table the canvas listens to. */
function keysFor(id: DesignShortcutId): string | undefined {
  const row = DESIGN_SHORTCUTS.find((s) => s.id === id);
  return row ? displayKeys(row) : undefined;
}

/** A one-click control. Its shortcut is both announced (`aria-keyshortcuts`) and shown in the tooltip. */
function ToolButton({
  label,
  keys,
  pressed,
  disabled,
  onClick,
  children,
}: {
  label: string;
  keys?: string;
  pressed?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <Tooltip label={keys ? `${label} (${keys})` : label} side="bottom">
      <button
        type="button"
        aria-label={label}
        aria-keyshortcuts={keys}
        aria-pressed={pressed}
        disabled={disabled}
        onClick={onClick}
        className={cn(
          "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-foreground transition-colors duration-1 ease-out hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-40",
          pressed && "bg-accent/15 text-accent-ink",
          focusRing,
        )}
      >
        {children}
      </button>
    </Tooltip>
  );
}

function Divider() {
  return <span aria-hidden="true" className="mx-1 h-5 w-px shrink-0 bg-hairline" />;
}

/**
 * The canvas's tools and commands in one row that wraps on a narrow screen. Tools with variants (shapes,
 * pen styles) and the less common commands sit behind menus; every menu is a `DesignMenu`, so the
 * keyboard and screen-reader behaviour is the same everywhere.
 */
export function DesignToolbar({
  tool, onToolChange, onCommand, onAlign, zoom, canUndo, canRedo, hasSelection, pages, pageId, onPageChange, onAddPage,
  onPresent, presenting, onDeviceWidth, deviceWidth, onToggleComments, commentsVisible, onZoom, drawStyle, onDrawStyle,
}: {
  tool: DesignTool;
  onToolChange: (tool: DesignTool) => void;
  onCommand: (command: DesignCommand) => void;
  onAlign: (mode: AlignMode) => void;
  zoom: number;
  canUndo: boolean;
  canRedo: boolean;
  /** Whether anything is selected: align, duplicate and delete need a selection. */
  hasSelection: boolean;
  pages: readonly DesignPage[];
  pageId: string | undefined;
  onPageChange: (id: string) => void;
  onAddPage: () => void;
  onPresent: () => void;
  presenting: boolean;
  onDeviceWidth: (width: number | null) => void;
  deviceWidth: number | null;
  onToggleComments: () => void;
  commentsVisible: boolean;
  onZoom: (scale: number) => void;
  drawStyle: DrawStyle;
  onDrawStyle: (style: DrawStyle) => void;
}) {
  const t = useT();
  const num = useNum();
  const page = pages.find((p) => p.id === pageId) ?? pages[0];
  const zoomPercent = num(Math.round(zoom * 100));
  const deviceText = deviceWidth ? num(deviceWidth) : t("design.preview.off");
  const toolButton = (id: DesignTool) => {
    const Icon = TOOL_ICON[id];
    return (
      <ToolButton key={id} label={t(`design.tool.${id}`)} keys={keysFor(TOOL_SHORTCUT[id])} pressed={tool === id} onClick={() => onToolChange(id)}>
        <Icon className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
    );
  };

  return (
    <div role="toolbar" aria-label={t("design.toolbar.label")} className="flex flex-wrap items-center gap-0.5 border-b border-hairline bg-card px-2 py-1">
      <select aria-label={t("design.page.label")} value={page?.id ?? ""} onChange={(e) => onPageChange(e.target.value)} className="field mr-1 h-8 max-w-32 px-2 text-xs">
        {pages.map((p) => (
          <option key={p.id} value={p.id}>
            {isIndexPage(p) ? t("design.index.title") : p.name}
          </option>
        ))}
      </select>
      <DesignMenu
        label={t("design.page.menu")}
        trigger={<Plus className="h-3.5 w-3.5" aria-hidden="true" />}
        items={<DesignMenuItem onSelect={onAddPage}>{t("design.page.new")}</DesignMenuItem>}
      />
      <Divider />
      {(["select", "pan", "text", "frame", "note", "rectangle"] as DesignTool[]).map(toolButton)}
      <DesignMenu
        label={t("design.tool.shapeMenu")}
        trigger={<Circle className="h-4 w-4" aria-hidden="true" />}
        items={
          <>
            <DesignMenuItem onSelect={() => onToolChange("ellipse")}>
              <Circle className="h-4 w-4" aria-hidden="true" />
              {t("design.tool.ellipse")}
            </DesignMenuItem>
            <DesignMenuItem onSelect={() => onToolChange("line")}>
              <Minus className="h-4 w-4" aria-hidden="true" />
              {t("design.tool.line")}
            </DesignMenuItem>
          </>
        }
      />
      {toolButton("draw")}
      <DesignMenu
        label={t("design.tool.drawMenu")}
        trigger={<PenLine className="h-3.5 w-3.5" aria-hidden="true" />}
        items={
          <>
            <DesignMenuItem onSelect={() => { onDrawStyle("pencil"); onToolChange("draw"); }}>
              {drawStyle === "pencil" ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <span className="w-3.5" />}
              {t("design.tool.pencil")}
            </DesignMenuItem>
            <DesignMenuItem onSelect={() => { onDrawStyle("marker"); onToolChange("draw"); }}>
              {drawStyle === "marker" ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <span className="w-3.5" />}
              {t("design.tool.marker")}
            </DesignMenuItem>
          </>
        }
      />
      <Divider />
      <ToolButton label={t("design.action.undo")} keys={keysFor("undo")} disabled={!canUndo} onClick={() => onCommand("undo")}>
        <Undo2 className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <ToolButton label={t("design.action.redo")} keys={keysFor("redo")} disabled={!canRedo} onClick={() => onCommand("redo")}>
        <Redo2 className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <DesignMenu
        label={t("design.toolbar.more")}
        trigger={<MessageSquarePlus className="h-3.5 w-3.5" aria-hidden="true" />}
        items={
          <>
            <DesignMenuItem onSelect={() => onToolChange("comment")}>
              <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
              {t("design.tool.comment")}
            </DesignMenuItem>
            <DesignMenuSeparator />
            {ALIGN.map(({ mode, icon: Icon, labelKey }) => (
              <DesignMenuItem key={mode} disabled={!hasSelection} onSelect={() => onAlign(mode)}>
                <Icon className="h-4 w-4" aria-hidden="true" />
                {t(labelKey)}
              </DesignMenuItem>
            ))}
            <DesignMenuSeparator />
            <DesignMenuItem disabled={!hasSelection} onSelect={() => onCommand("duplicate")}>
              <Copy className="h-4 w-4" aria-hidden="true" />
              {t("design.action.duplicate")}
            </DesignMenuItem>
            <DesignMenuItem disabled={!hasSelection} onSelect={() => onCommand("delete")}>
              <Trash2 className="h-4 w-4" aria-hidden="true" />
              {t("design.action.delete")}
            </DesignMenuItem>
            <DesignMenuItem onSelect={() => onCommand("help")}>
              <Keyboard className="h-4 w-4" aria-hidden="true" />
              {t("design.keys.help")}
            </DesignMenuItem>
          </>
        }
      />
      <ToolButton label={commentsVisible ? t("design.comments.hide") : t("design.comments.show")} pressed={commentsVisible} onClick={onToggleComments}>
        <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <span className="flex-1" />
      <ToolButton label={t("design.present.open")} pressed={presenting} onClick={onPresent}>
        <Play className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <DesignMenu
        label={t("design.preview.device", { width: deviceText })}
        trigger={<span>{deviceText}</span>}
        items={
          <>
            <DesignMenuItem onSelect={() => onDeviceWidth(null)}>
              <Monitor className="h-4 w-4" aria-hidden="true" />
              {t("design.preview.off")}
            </DesignMenuItem>
            {[375, 768, 1280].map((w) => (
              <DesignMenuItem key={w} onSelect={() => onDeviceWidth(w)}>
                {t("design.preview.width", { width: num(w) })}
              </DesignMenuItem>
            ))}
          </>
        }
      />
      <Divider />
      <DesignMenu
        label={t("design.zoom.menu", { percent: zoomPercent })}
        trigger={<span className="min-w-10 text-center text-xs tabular-nums text-muted-foreground">{zoomPercent}%</span>}
        items={
          <>
            <DesignMenuItem onSelect={() => onCommand("zoomIn")}>
              <ZoomIn className="h-4 w-4" aria-hidden="true" />
              {t("design.action.zoomIn")}
            </DesignMenuItem>
            <DesignMenuItem onSelect={() => onCommand("zoomOut")}>
              <ZoomOut className="h-4 w-4" aria-hidden="true" />
              {t("design.action.zoomOut")}
            </DesignMenuItem>
            <DesignMenuItem
              onSelect={() => onCommand("zoomFit")}
              title={t("design.action.zoomFitAt", { percent: zoomPercent })}
            >
              {t("design.action.zoomFit")}
            </DesignMenuItem>
            <DesignMenuSeparator />
            {([50, 100, 200] as const).map((percent) => (
              <DesignMenuItem key={percent} onSelect={() => onZoom(percent / 100)}>
                {`${num(percent)}%`}
              </DesignMenuItem>
            ))}
          </>
        }
      />
      <ToolButton label={t("design.action.zoomOut")} keys={keysFor("zoomOut")} onClick={() => onCommand("zoomOut")}>
        <ZoomOut className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <ToolButton label={t("design.action.zoomIn")} keys={keysFor("zoomIn")} onClick={() => onCommand("zoomIn")}>
        <ZoomIn className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
    </div>
  );
}
