import { useState } from "react";

import { formatElapsed } from "@/components/code/Elapsed";
import type { CodeToolEvent } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** A tool call, compactly: what ran, on what, whether it worked — and what it said.
 *
 *  The observation used to live in a native `title=`, which cannot be selected, cannot be copied,
 *  and is truncated again by the OS at a length we do not control. A failing command's output is
 *  the thing a person most wants to paste somewhere, and it was the one thing they could not take.
 *
 *  It stays FOLDED, and the summary says the output is clipped. The clip is real — the server keeps
 *  the head and the tail of 400 characters — and this deliberately does not offer "full output",
 *  because there is no route on our side that has it. Promising it and then showing the same 400
 *  characters would be worse than the tooltip. */
export function ToolRow({
  tool,
  onOpenFile,
  detailed = false,
}: {
  tool: CodeToolEvent;
  onOpenFile?: (p: string) => void;
  /** The Detailed transcript view: output unfolded and every argument shown, not only the first. */
  detailed?: boolean;
}) {
  const t = useT();
  const duration = tool.duration_seconds;
  const primary = String(Object.values(tool.arguments)[0] ?? "");
  const designAction = tool.name === "design" ? String(tool.arguments.action ?? "") : "";
  let designChangeCount = 0;
  if (designAction === "apply" || designAction === "propose") {
    try {
      const result: unknown = JSON.parse(tool.observation || "{}");
      if (typeof result === "object" && result !== null && "applied" in result) {
        designChangeCount = Number(result.applied) || 0;
      }
    } catch {
      designChangeCount = 0;
    }
  }
  const toolLabel = tool.name !== "design"
    ? tool.name
    : designAction === "create"
      ? t("code.chat.design.created")
      : designAction === "apply" || designAction === "propose"
        ? t("code.chat.design.updated", { n: designChangeCount })
        : designAction.startsWith("export")
          ? t("code.chat.design.exported")
          : t("code.chat.design.generic");
  const [copied, setCopied] = useState(false);
  // A path the agent touched is the handle for looking at it. With the file tree gone this is
  // how the viewer opens — the same way you would click a filename in a terminal that linkifies
  // them. Heuristic on purpose: an argument that looks like a path is offered as one, and one
  // that is not stays plain text rather than becoming a button that does nothing.
  const looksLikePath = /[/\.]/.test(primary) && !primary.includes(" ");
  return (
    <div className="font-mono text-xs">
      <div className="flex items-baseline gap-2">
        <span className={cn("shrink-0", tool.ok ? "text-ok" : "text-bad")}>
          {tool.ok ? "✓" : "✗"}
        </span>
        <span className="shrink-0 text-foreground/80">{toolLabel}</span>
        {duration !== undefined ? (
          <span className="shrink-0 tabular-nums text-muted-foreground" data-testid="step-duration">
            {formatElapsed(duration, t)}
          </span>
        ) : null}
        {looksLikePath && onOpenFile ? (
          <button
            type="button"
            onClick={() => onOpenFile(primary)}
            className="truncate text-left text-muted-foreground underline decoration-dotted hover:text-accent"
          >
            {primary}
          </button>
        ) : (
          <span className="truncate text-muted-foreground">{primary}</span>
        )}
      </div>
      {detailed && Object.keys(tool.arguments).length > 0 ? (
        <pre data-testid="tool-arguments" className="ml-5 mt-0.5 max-h-48 overflow-auto whitespace-pre-wrap rounded-chip bg-surface-2 p-2 text-muted-foreground">
          {JSON.stringify(tool.arguments, null, 2)}
        </pre>
      ) : null}
      {tool.observation ? (
        <details className="ml-5 mt-0.5" open={detailed || !tool.ok}>
          {/* Open by default when the tool FAILED. That is the case where the output is the reason
              the user is reading this screen at all; for a successful `read_file` it is noise —
              unless the person chose the Detailed transcript view, which asks for exactly that. */}
          <summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground">
            {t("code.chat.tool.output")}
          </summary>
          <div className="relative">
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-chip bg-surface-2 p-2 pr-16 text-muted-foreground">
              {tool.observation}
            </pre>
            <button
              type="button"
              className="absolute right-1 top-2 rounded-chip bg-surface-1 px-1.5 py-0.5 text-muted-foreground hover:text-foreground"
              onClick={() => {
                // Best-effort: `navigator.clipboard` is absent in a non-secure context and rejects
                // when the window is not focused. Failing silently leaves the button looking
                // broken, so the label only changes once the write actually resolved.
                void navigator.clipboard
                  ?.writeText(tool.observation)
                  .then(() => setCopied(true))
                  .catch(() => setCopied(false));
              }}
            >
              {t(copied ? "code.chat.tool.copied" : "code.chat.tool.copy")}
            </button>
          </div>
        </details>
      ) : null}
    </div>
  );
}
