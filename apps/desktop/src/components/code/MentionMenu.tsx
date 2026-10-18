import { useMemo, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText, Folder, MessagesSquare, PenTool, X } from "lucide-react";

import { findPaths, listCodeSessions, listDesigns } from "@/lib/api";
import type { ContextEntry } from "@/lib/context-items";
import type { TFunc } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The composer's `@` list: files and folders of this project, designs, and recent conversations.
 *
 * Gap #2 against the editors people compare us with (study 34): without it the model was told "fix
 * the login form" and spent its first tool calls finding the file the person already knew. Picking
 * an entry takes the `@word` out of the box and puts a chip above it; the chip goes out as `context`
 * with the next turn. Keyboard first — arrows move, Enter or Tab picks, Escape closes — and the box
 * keeps focus throughout, pointing at the active option through `aria-activedescendant`.
 *
 * Open only while the word under the caret is an `@` mention (at the start of the box or after a
 * space, so an e-mail address is not one) and only when something matches: an Enter with nothing to
 * pick must still send the message, the rule the `/` list follows.
 */

/** The longest query worth asking about; past it the word is not a mention being typed. */
const MAX_QUERY = 120;
const FILES_SHOWN = 8;
const OTHERS_SHOWN = 5;

/** The `@` word the caret is in: its text and where the `@` is, or null when there is none. */
export function mentionQuery(draft: string, caret: number): { query: string; start: number } | null {
  const before = draft.slice(0, caret);
  const at = before.lastIndexOf("@");
  if (at < 0) return null;
  if (at > 0 && !/\s/.test(before[at - 1])) return null;
  const query = before.slice(at + 1);
  if (/\s/.test(query) || query.length > MAX_QUERY) return null;
  return { query, start: at };
}

/** How well `text` answers `query` (higher is better), or null. Letters in order count, as in the
 *  server's path finder, so `lgn` finds "Login". */
export function matchScore(query: string, text: string): number | null {
  if (!query) return 0;
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  if (t.startsWith(q)) return 300 - t.length / 100;
  if (t.includes(q)) return 200 - t.length / 100;
  let at = 0;
  for (const char of t) if (at < q.length && char === q[at]) at += 1;
  return at === q.length ? 100 - t.length / 100 : null;
}

type Group = "files" | "designs" | "conversations";

export interface MentionOption {
  key: string;
  group: Group;
  entry: ContextEntry;
}

export interface MentionMenuState {
  open: boolean;
  options: MentionOption[];
  active: number;
  setActive: (i: number) => void;
  /** Handle a key while the list is open; true when it was the list's key and must not reach the box. */
  onKeyDown: (ev: KeyboardEvent<HTMLTextAreaElement>) => boolean;
  pick: (option: MentionOption) => void;
}

function best<T>(items: readonly T[], query: string, text: (item: T) => string, n: number): T[] {
  return items
    .map((item) => ({ item, score: matchScore(query, text(item)) }))
    .filter((x): x is { item: T; score: number } => x.score !== null)
    .sort((a, b) => b.score - a.score)
    .slice(0, n)
    .map((x) => x.item);
}

export function useMentionMenu({
  draft,
  caret,
  workspace,
  sessionId,
  setDraft,
  onPick,
}: {
  draft: string;
  caret: number;
  workspace: string;
  /** The conversation being typed in, left out of its own list. */
  sessionId: string | null;
  setDraft: (text: string, caret: number) => void;
  onPick: (entry: ContextEntry) => void;
}): MentionMenuState {
  const [active, setActive] = useState(0);
  // Escape closes the list for the draft it was pressed on; typing anything else opens it again.
  const [dismissed, setDismissed] = useState<string | null>(null);
  const mention = mentionQuery(draft, caret);
  const asking = mention !== null;
  const query = mention?.query ?? "";
  // Nothing is asked for until an `@` is typed: a conversation in which nobody mentions anything
  // never lists the project. The design and conversation lists share the screens' caches.
  const files = useQuery({
    queryKey: ["mention-paths", workspace, query],
    queryFn: () => findPaths(workspace, query, FILES_SHOWN),
    enabled: asking,
    staleTime: 10_000,
  });
  const designs = useQuery({ queryKey: ["designs"], queryFn: () => listDesigns(), enabled: asking });
  const sessions = useQuery({
    queryKey: ["code-sessions"],
    queryFn: () => listCodeSessions(),
    enabled: asking,
  });

  const options = useMemo<MentionOption[]>(() => {
    if (!asking) return [];
    const out: MentionOption[] = [];
    for (const e of files.data?.entries ?? []) {
      const item = e.is_dir
        ? ({ kind: "folder", path: e.path } as const)
        : ({ kind: "file", path: e.path } as const);
      out.push({
        key: `${item.kind}:${e.path}`,
        group: "files",
        entry: { item, label: e.is_dir ? `${e.path}/` : e.path },
      });
    }
    for (const d of best(designs.data ?? [], query, (x) => x.title, OTHERS_SHOWN)) {
      out.push({ key: `design:${d.id}`, group: "designs", entry: { item: { kind: "design", id: d.id }, label: d.title } });
    }
    const recent = [...(sessions.data ?? [])]
      .filter((s) => s.id !== sessionId)
      .sort((a, b) => b.updated_at - a.updated_at);
    for (const s of best(recent, query, (x) => x.title, OTHERS_SHOWN)) {
      out.push({
        key: `conversation:${s.id}`,
        group: "conversations",
        entry: { item: { kind: "conversation", id: s.id }, label: s.title || s.id },
      });
    }
    return out;
  }, [asking, files.data, designs.data, sessions.data, query, sessionId]);

  const open = options.length > 0 && dismissed !== draft;
  const current = Math.min(active, Math.max(options.length - 1, 0));

  const pick = (option: MentionOption) => {
    if (!mention) return;
    // The `@word` goes; what was typed around it stays where it was.
    const next = draft.slice(0, mention.start) + draft.slice(caret);
    setDraft(next, mention.start);
    setActive(0);
    onPick(option.entry);
  };

  const onKeyDown = (ev: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!open) return false;
    if (ev.key === "ArrowDown") {
      setActive((current + 1) % options.length);
    } else if (ev.key === "ArrowUp") {
      setActive((current - 1 + options.length) % options.length);
    } else if ((ev.key === "Enter" && !ev.shiftKey) || ev.key === "Tab") {
      pick(options[current]);
    } else if (ev.key === "Escape") {
      setDismissed(draft);
    } else {
      return false;
    }
    ev.preventDefault();
    return true;
  };

  return { open, options, active: current, setActive, onKeyDown, pick };
}

const GROUP_LABEL: Record<Group, string> = {
  files: "composer.mention.files",
  designs: "composer.mention.designs",
  conversations: "composer.mention.conversations",
};

function KindIcon({ entry }: { entry: ContextEntry }) {
  const cls = "h-3.5 w-3.5 shrink-0";
  switch (entry.item.kind) {
    case "folder":
      return <Folder className={cls} aria-hidden />;
    case "design":
      return <PenTool className={cls} aria-hidden />;
    case "conversation":
      return <MessagesSquare className={cls} aria-hidden />;
    default:
      return <FileText className={cls} aria-hidden />;
  }
}

export function MentionMenu({ id, state, t }: { id: string; state: MentionMenuState; t: TFunc }) {
  if (!state.open) return null;
  const groups = (["files", "designs", "conversations"] as const).filter((g) =>
    state.options.some((o) => o.group === g),
  );
  return (
    <div
      id={id}
      role="listbox"
      aria-label={t("composer.mention.title")}
      className="floating mb-1.5 max-h-72 overflow-y-auto p-1.5"
    >
      {groups.map((group) => (
        <div key={group} role="group" aria-label={t(GROUP_LABEL[group])}>
          <div aria-hidden className="px-2.5 pb-0.5 pt-1.5 text-xs text-muted-foreground">
            {t(GROUP_LABEL[group])}
          </div>
          {state.options.map((option, i) =>
            option.group !== group ? null : (
              <div
                key={option.key}
                id={`${id}-${i}`}
                role="option"
                aria-selected={i === state.active}
                onMouseEnter={() => state.setActive(i)}
                // mousedown, not click: the box would lose focus first and the next key would go nowhere.
                onMouseDown={(ev) => {
                  ev.preventDefault();
                  state.pick(option);
                }}
                className={cn(
                  "flex cursor-default items-center gap-2 rounded-chip px-2.5 py-1.5 text-sm",
                  i === state.active && "bg-surface-hover",
                )}
              >
                <KindIcon entry={option.entry} />
                <span className={cn("truncate", option.group === "files" && "font-mono")}>
                  {option.entry.label}
                </span>
              </div>
            ),
          )}
        </div>
      ))}
    </div>
  );
}

/** The mentions this message will carry, each removable. Nothing at all while there are none. */
export function MentionChips({
  items,
  onRemove,
  t,
}: {
  items: readonly ContextEntry[];
  onRemove: (index: number) => void;
  t: TFunc;
}) {
  if (items.length === 0) return null;
  return (
    <ul aria-label={t("composer.mention.chips")} className="flex flex-wrap gap-1.5 pb-1.5">
      {items.map((entry, i) => (
        <li
          key={`${entry.item.kind}:${entry.label}:${i}`}
          className="flex items-center gap-1.5 rounded-chip border border-border px-2 py-1 text-xs text-muted-foreground"
        >
          <KindIcon entry={entry} />
          <span className="max-w-56 truncate">{chipLabel(entry, t)}</span>
          <button
            type="button"
            onClick={() => onRemove(i)}
            aria-label={t("composer.mention.remove", { name: entry.label })}
            className="text-muted-foreground hover:text-bad"
          >
            <X className="h-3 w-3" />
          </button>
        </li>
      ))}
    </ul>
  );
}

function chipLabel(entry: ContextEntry, t: TFunc): string {
  const { item } = entry;
  if (item.kind === "file" && item.start_line != null && item.end_line != null) {
    return `${entry.label} ${t("composer.mention.lines", { from: item.start_line, to: item.end_line })}`;
  }
  return entry.label;
}
