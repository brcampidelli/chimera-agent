import type { CodeToolEvent } from "@/lib/api";

/**
 * The files a turn produced that are worth opening beside the conversation (study 29, P6.3).
 *
 * The viewer opened only when someone clicked a path in the tree, so a chart, a page or a report the
 * agent had just written sat in the workspace while the answer described it. These are what the
 * receipt offers to open: deliverables the viewer can show — a page, an image, an SVG's source, and
 * since this change a document's text — not every file the turn edited.
 *
 * Read off the turn's own tool calls, which a reopened conversation still has (the replay keeps them
 * with their arguments), and off its edit frames, which only a live turn has. A call that failed or
 * was refused wrote nothing and offers nothing.
 */
export const OPENABLE_OUTPUT_EXTS = new Set([
  "html",
  "htm",
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
  "pdf",
  "docx",
  "xlsx",
  "pptx",
]);

/** The writers whose output is a deliverable: the argument naming the file, and the tool's own default
 *  when it is absent (`render_chart` writes `chart.<format>`, `generate_image` `generated_image.png`). */
const WRITERS: Record<string, { arg: string; fallback?: (args: Record<string, unknown>) => string }> = {
  write_file: { arg: "path" },
  create_document: { arg: "path" },
  render_chart: {
    arg: "out",
    fallback: (args) => `chart.${String(args.format || "html").toLowerCase()}`,
  },
  generate_image: { arg: "out", fallback: () => "generated_image.png" },
};

/** At most this many buttons under a receipt: a turn that wrote forty images is better read in the tree. */
export const MAX_OUTPUTS = 6;

function extOf(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? "";
  return name.includes(".") ? name.split(".").pop()!.toLowerCase() : "";
}

export function turnOutputs(
  tools: readonly CodeToolEvent[],
  edits: readonly { path: string }[] = [],
): string[] {
  const out: string[] = [];
  const add = (raw: unknown) => {
    const path = typeof raw === "string" ? raw.trim() : "";
    if (path && OPENABLE_OUTPUT_EXTS.has(extOf(path)) && !out.includes(path)) out.push(path);
  };
  for (const tool of tools) {
    const writer = WRITERS[tool.name];
    if (!writer || !tool.ok) continue;
    const args = (tool.arguments ?? {}) as Record<string, unknown>;
    add(args[writer.arg] || writer.fallback?.(args));
  }
  for (const edit of edits) add(edit.path);
  return out.slice(0, MAX_OUTPUTS);
}
