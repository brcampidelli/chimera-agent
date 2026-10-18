/** The command palette's conversation entries.
 *
 * DESIGN.md promised "every conversation by title" behind ⌘K, and the comment above the palette's
 * command list in `App.tsx` said the same, while the list held destinations, tabs and layout
 * commands only: no conversation was ever in it. Typing a conversation's name found nothing, and an
 * empty result reads as "no such conversation" — the opposite of the truth.
 */

import type { Command } from "@/components/shell/CommandPalette";
import type { CodeSessionMeta } from "@/lib/api";
import { basename } from "@/lib/projects";

/** Shown before anything is typed: the recent ones. The rest answer a search. */
export const RECENT_IN_PALETTE = 8;

type Translate = (key: string, params?: Record<string, string | number>) => string;

export type PaletteSession = Pick<CodeSessionMeta, "id" | "title" | "workspace" | "updated_at"> &
  Partial<Pick<CodeSessionMeta, "project" | "project_name">>;

function projectLabel(session: PaletteSession, t: Translate): string {
  const root = session.project || session.workspace;
  return session.project_name || (root ? basename(root) : t("code.sessions.defaultProject"));
}

/** One entry per conversation, newest first. Archived ones come last, answer only a search, and say
 *  "Archived" beside the project — out of the way is what archiving asked for, gone is not. */
export function conversationCommands(
  active: readonly PaletteSession[],
  archived: readonly PaletteSession[],
  t: Translate,
  open: (session: PaletteSession) => void,
): Command[] {
  const archivedIds = new Set(archived.map((s) => s.id));
  const newest = (a: PaletteSession, b: PaletteSession) => b.updated_at - a.updated_at;
  const live = active.filter((s) => !archivedIds.has(s.id)).sort(newest);
  const entry = (session: PaletteSession, index: number, isArchived: boolean): Command => ({
    id: `${isArchived ? "archived" : "conversation"}-${session.id}`,
    label: session.title || t("code.sessions.untitled"),
    group: isArchived
      ? t("palette.conversation.archivedIn", { project: projectLabel(session, t) })
      : projectLabel(session, t),
    searchOnly: isArchived || index >= RECENT_IN_PALETTE,
    run: () => open(session),
  });
  return [
    ...live.map((s, i) => entry(s, i, false)),
    ...[...archived].sort(newest).map((s, i) => entry(s, i, true)),
  ];
}
