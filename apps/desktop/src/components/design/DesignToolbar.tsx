import {
  AlignCenterHorizontal,
  AlignCenterVertical,
  AlignEndHorizontal,
  AlignEndVertical,
  AlignStartHorizontal,
  AlignStartVertical,
  Circle,
  Copy,
  Keyboard,
  MessageSquarePlus,
  Minus,
  MousePointer2,
  PenLine,
  Redo2,
  Square,
  Trash2,
  Type,
  Undo2,
  ZoomIn,
  ZoomOut,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";

import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import type { AlignMode } from "@/lib/design/geometry";
import { DESIGN_SHORTCUTS, displayKeys, type DesignShortcutId } from "@/lib/design/shortcuts";
import { TOOLS, TOOL_SHORTCUT, type DesignCommand, type DesignTool } from "@/lib/design/tools";
import { useNum, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const TOOL_ICON: Record<DesignTool, LucideIcon> = {
  select: MousePointer2,
  text: Type,
  rectangle: Square,
  ellipse: Circle,
  line: Minus,
  draw: PenLine,
  comment: MessageSquarePlus,
};

const ALIGN: { mode: AlignMode; icon: LucideIcon; labelKey: string }[] = [
  { mode: "left", icon: AlignStartVertical, labelKey: "design.align.left" },
  { mode: "center", icon: AlignCenterVertical, labelKey: "design.align.center" },
  { mode: "right", icon: AlignEndVertical, labelKey: "design.align.right" },
  { mode: "top", icon: AlignStartHorizontal, labelKey: "design.align.top" },
  { mode: "middle", icon: AlignCenterHorizontal, labelKey: "design.align.middle" },
  { mode: "bottom", icon: AlignEndHorizontal, labelKey: "design.align.bottom" },
];

function keysFor(id: DesignShortcutId): string | undefined {
  const row = DESIGN_SHORTCUTS.find((s) => s.id === id);
  return row ? displayKeys(row) : undefined;
}

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
          "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-foreground transition-colors duration-1 ease-out",
          "hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-40",
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
 * The canvas's tools and commands, in one row that wraps on a narrow screen. Every button has a
 * real name (`aria-label`) and its shortcut both announced (`aria-keyshortcuts`) and shown in the
 * tooltip; the tools are toggle buttons, so the current one reads as "pressed".
 */
export function DesignToolbar({
  tool,
  onToolChange,
  onCommand,
  onAlign,
  zoom,
  canUndo,
  canRedo,
  hasSelection,
}: {
  tool: DesignTool;
  onToolChange: (tool: DesignTool) => void;
  onCommand: (command: DesignCommand) => void;
  onAlign: (mode: AlignMode) => void;
  zoom: number;
  canUndo: boolean;
  canRedo: boolean;
  hasSelection: boolean;
}) {
  const t = useT();
  const num = useNum();
  return (
    <div role="toolbar" aria-label={t("design.toolbar.label")} className="flex flex-wrap items-center gap-0.5 border-b border-hairline bg-card px-2 py-1">
      {TOOLS.map((id) => {
        const Icon = TOOL_ICON[id];
        return (
          <ToolButton key={id} label={t(`design.tool.${id}`)} keys={keysFor(TOOL_SHORTCUT[id])} pressed={tool === id} onClick={() => onToolChange(id)}>
            <Icon className="h-4 w-4" aria-hidden="true" />
          </ToolButton>
        );
      })}
      <Divider />
      <ToolButton label={t("design.action.undo")} keys={keysFor("undo")} disabled={!canUndo} onClick={() => onCommand("undo")}>
        <Undo2 className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <ToolButton label={t("design.action.redo")} keys={keysFor("redo")} disabled={!canRedo} onClick={() => onCommand("redo")}>
        <Redo2 className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <Divider />
      {ALIGN.map(({ mode, icon: Icon, labelKey }) => (
        <ToolButton key={mode} label={t(labelKey)} disabled={!hasSelection} onClick={() => onAlign(mode)}>
          <Icon className="h-4 w-4" aria-hidden="true" />
        </ToolButton>
      ))}
      <Divider />
      <ToolButton label={t("design.action.duplicate")} keys={keysFor("duplicate")} disabled={!hasSelection} onClick={() => onCommand("duplicate")}>
        <Copy className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <ToolButton label={t("design.action.delete")} keys={keysFor("delete")} disabled={!hasSelection} onClick={() => onCommand("delete")}>
        <Trash2 className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <Divider />
      <ToolButton label={t("design.action.zoomOut")} keys={keysFor("zoomOut")} onClick={() => onCommand("zoomOut")}>
        <ZoomOut className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <button
        type="button"
        onClick={() => onCommand("zoomFit")}
        aria-label={t("design.action.zoomFitAt", { percent: num(Math.round(zoom * 100)) })}
        className={cn("h-8 min-w-12 rounded-lg px-1 text-xs tabular-nums text-muted-foreground transition-colors duration-1 ease-out hover:bg-surface-hover", focusRing)}
      >
        {`${num(Math.round(zoom * 100))}%`}
      </button>
      <ToolButton label={t("design.action.zoomIn")} keys={keysFor("zoomIn")} onClick={() => onCommand("zoomIn")}>
        <ZoomIn className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
      <Divider />
      <ToolButton label={t("design.keys.help")} keys={keysFor("help")} onClick={() => onCommand("help")}>
        <Keyboard className="h-4 w-4" aria-hidden="true" />
      </ToolButton>
    </div>
  );
}
