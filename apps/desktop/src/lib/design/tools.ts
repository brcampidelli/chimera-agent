import type { DesignShortcutId } from "@/lib/design/shortcuts";

export type DesignTool = "select" | "text" | "rectangle" | "ellipse" | "line" | "draw" | "comment";

export const TOOLS: readonly DesignTool[] = ["select", "text", "rectangle", "ellipse", "line", "draw", "comment"];

export const TOOL_SHORTCUT: Record<DesignTool, DesignShortcutId> = {
  select: "toolSelect",
  text: "toolText",
  rectangle: "toolRectangle",
  ellipse: "toolEllipse",
  line: "toolLine",
  draw: "toolDraw",
  comment: "toolComment",
};

export function toolForShortcut(id: DesignShortcutId): DesignTool | undefined {
  return TOOLS.find((tool) => TOOL_SHORTCUT[tool] === id);
}

/** Commands the canvas hands up to the workspace, because the toolbar offers them too. */
export type DesignCommand = "undo" | "redo" | "duplicate" | "delete" | "zoomIn" | "zoomOut" | "zoomFit" | "help";

export const COMMANDS: readonly DesignCommand[] = ["undo", "redo", "duplicate", "delete", "zoomIn", "zoomOut", "zoomFit", "help"];

export function isCommand(id: string): id is DesignCommand {
  return (COMMANDS as readonly string[]).includes(id);
}
