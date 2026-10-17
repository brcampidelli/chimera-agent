import type { TFunc } from "@/lib/i18n";

/** Where a Markdown command or subagent came from, in the reader's words: the project's folder, the
 *  owner's own files, or a named plugin. A plugin is named because its text is somebody else's, and
 *  a turn it starts is marked as having read untrusted content. */
export function definitionSourceLabel(t: TFunc, source: string): string {
  if (source === "project") return t("definitions.source.project");
  if (source === "owner") return t("definitions.source.owner");
  if (source.startsWith("plugin:")) {
    return t("definitions.source.plugin", { name: source.slice("plugin:".length) });
  }
  return source;
}
