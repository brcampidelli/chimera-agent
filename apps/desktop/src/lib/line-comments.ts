import { useEffect, useRef, useSyncExternalStore } from "react";

/** Comments the owner leaves on lines of a diff, held per conversation until they are sent.
 *
 * A pending comment is a draft about someone else's edit, so it lives with the conversation it is
 * for: switching to another conversation and back finds it still there, and it goes out in one
 * message when the owner presses send (or Ctrl/Cmd+Enter). Sending clears it, and only once the
 * conversation has accepted the message.
 *
 * The map is the source of truth while the app runs; localStorage is only how it survives a
 * reload. When storage is blocked the comments still work for the session.
 */

export type LineSide = "new" | "old";
export type LineMarker = "+" | "-" | " ";

/** A diff line a comment can be attached to. `origin` says which diff it was read from: `edit:<i>:<j>`
 *  for edit `j` of exchange `i` in the transcript, or `git` for the Changes card. */
export interface LineRef {
  origin: string;
  file: string;
  side: LineSide;
  line: number;
  marker: LineMarker;
  content: string;
}

export interface LineComment extends LineRef {
  id: string;
  text: string;
}

export interface LineCommentLabels {
  header: (count: number) => string;
  added: string;
  removed: string;
  context: string;
  snippet: string;
  comment: string;
  fromEdit: (turn: number) => string;
  fromGit: string;
  stale: (turn: number) => string;
  blank: string;
}

type Translate = (key: string, vars?: Record<string, string | number>) => string;

const PREFIX = "chimera:code:line-comments:";
const EVENT = "chimera:line-comments:send";
const NOTICE_MS = 4000;
const EMPTY: readonly LineComment[] = [];

const byConversation = new Map<string, readonly LineComment[]>();
const loaded = new Set<string>();
const notices = new Map<string, string>();
const noticeTimers = new Map<string, ReturnType<typeof setTimeout>>();
const listeners = new Set<() => void>();

function emit(): void {
  for (const notify of listeners) notify();
}

export function lineCommentId(ref: Pick<LineRef, "origin" | "file" | "side" | "line">): string {
  return `${ref.origin}\u0000${ref.file}\u0000${ref.side}\u0000${ref.line}`;
}

/** The turn (1-based) an `edit:<i>:<j>` origin belongs to; null for any other origin. */
export function editTurn(origin: string): number | null {
  const match = /^edit:(\d+):\d+$/.exec(origin);
  return match ? Number(match[1]) + 1 : null;
}

/** The last turn that edited each file, so a comment on an older edit can be marked as possibly
 *  outdated. */
export function latestEditTurns(exchanges: readonly { edits: readonly { path: string }[] }[]): Record<string, number> {
  const latest: Record<string, number> = {};
  exchanges.forEach((exchange, index) => {
    for (const edit of exchange.edits) latest[edit.path] = index + 1;
  });
  return latest;
}

function load(sessionId: string): readonly LineComment[] {
  if (loaded.has(sessionId)) return byConversation.get(sessionId) ?? EMPTY;
  loaded.add(sessionId);
  try {
    const raw = localStorage.getItem(PREFIX + sessionId);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (Array.isArray(parsed)) byConversation.set(sessionId, parsed as LineComment[]);
    }
  } catch {
    // Unreadable or blocked storage: the comments start empty and live in memory only.
  }
  return byConversation.get(sessionId) ?? EMPTY;
}

function store(sessionId: string, items: readonly LineComment[]): void {
  if (items.length === 0) byConversation.delete(sessionId);
  else byConversation.set(sessionId, items);
  try {
    if (items.length === 0) localStorage.removeItem(PREFIX + sessionId);
    else localStorage.setItem(PREFIX + sessionId, JSON.stringify(items));
  } catch {
    // Storage blocked: the comments stay in memory for this session.
  }
  emit();
}

/** The pending comments of a conversation, in the order they were written. */
export function readLineComments(sessionId: string): readonly LineComment[] {
  return load(sessionId);
}

/** Saves the comment on a line; saving the same line of the same diff again replaces it. Empty text
 *  removes it. */
export function saveLineComment(sessionId: string, ref: LineRef, text: string): void {
  const id = lineCommentId(ref);
  const current = load(sessionId);
  const body = text.trim();
  const without = current.filter((item) => item.id !== id);
  if (body === "") {
    if (without.length !== current.length) store(sessionId, without);
    return;
  }
  const comment: LineComment = { ...ref, id, text: body };
  const replaced = current.some((item) => item.id === id);
  store(sessionId, replaced ? current.map((item) => (item.id === id ? comment : item)) : [...current, comment]);
}

export function removeLineComment(sessionId: string, id: string): void {
  const current = load(sessionId);
  const next = current.filter((item) => item.id !== id);
  if (next.length !== current.length) store(sessionId, next);
}

export function clearLineComments(sessionId: string): void {
  if (load(sessionId).length > 0) store(sessionId, []);
}

/** A conversation was deleted: its pending comments and any notice go with it. */
export function forgetLineComments(sessionId: string): void {
  clearLineComments(sessionId);
  clearTimeout(noticeTimers.get(sessionId));
  noticeTimers.delete(sessionId);
  if (notices.delete(sessionId)) emit();
}

/** Markdown inline code that holds `text` whatever backticks it contains: the delimiter is one
 *  backtick longer than the longest run inside. */
export function codeSpan(text: string): string {
  const longest = Math.max(0, ...(text.match(/`+/g) ?? []).map((run) => run.length));
  const fence = "`".repeat(longest + 1);
  const pad = text.startsWith("`") || text.endsWith("`") ? " " : "";
  return `${fence}${pad}${text}${pad}${fence}`;
}

/** Free text that is not inside a code span: backslashes and backticks are escaped, so a backslash
 *  cannot escape the escape and no fence can open. */
export function escapeText(text: string): string {
  return text.replace(/[\\`]/g, (ch) => `\\${ch}`);
}

export function kindLabel(marker: LineMarker, labels: Pick<LineCommentLabels, "added" | "removed" | "context">): string {
  return marker === "+" ? labels.added : marker === "-" ? labels.removed : labels.context;
}

const SIDE_ORDER: Record<LineSide, number> = { new: 0, old: 1 };

/** The message the pending comments become: one block per comment, grouped by file, with where the
 *  line is, which diff it came from, what it said, and a note when that edit may be outdated. Pure,
 *  so the text the agent receives is testable.
 *
 *  A comment on an older edit is annotated, not dropped: the owner may still mean it, and a silent
 *  drop would lose the comment. The note tells the agent to check it against the file first. */
export function formatLineComments(
  items: readonly LineComment[],
  labels: LineCommentLabels,
  latest: Record<string, number> = {},
): string {
  const ordered = [...items].sort(
    (a, b) => a.file.localeCompare(b.file) || a.line - b.line || SIDE_ORDER[a.side] - SIDE_ORDER[b.side],
  );
  const blocks = ordered.map((item, index) => {
    const turn = editTurn(item.origin);
    const origin = turn === null ? labels.fromGit : labels.fromEdit(turn);
    const body = escapeText(item.text).split("\n").join("\n   ");
    const lines = [
      `${index + 1}. ${item.file}:${item.line} (${kindLabel(item.marker, labels)}; ${origin})`,
      `   ${labels.snippet}: ${item.content.trim() === "" ? labels.blank : codeSpan(item.content.trim())}`,
      `   ${labels.comment}: ${body}`,
    ];
    if (turn !== null && (latest[item.file] ?? 0) > turn) lines.push(`   ${labels.stale(turn)}`);
    return lines.join("\n");
  });
  return [labels.header(items.length), "", ...blocks].join("\n");
}

/** The labels a conversation formats its comments with, in the owner's language. */
export function lineCommentLabels(t: Translate): LineCommentLabels {
  return {
    header: (count) => t("code.lineComments.header", { n: count }),
    added: t("code.lineComments.added"),
    removed: t("code.lineComments.removed"),
    context: t("code.lineComments.context"),
    snippet: t("code.lineComments.snippet"),
    comment: t("code.lineComments.comment"),
    fromEdit: (turn) => t("code.lineComments.fromEdit", { n: turn }),
    fromGit: t("code.lineComments.fromGit"),
    stale: (turn) => t("code.lineComments.stale", { n: turn }),
    blank: t("code.lineComments.blank"),
  };
}

/** The pending comments as one message, or null when there are none. It does NOT clear them: the
 *  caller clears them once the conversation has accepted the message. */
export function buildLineCommentsMessage(
  sessionId: string,
  labels: LineCommentLabels,
  latest: Record<string, number>,
): string | null {
  const items = load(sessionId);
  if (items.length === 0) return null;
  return formatLineComments(items, labels, latest);
}

/** Says briefly why the comments did not go out (for example, a turn is running). It clears itself. */
export function setLineCommentsNotice(sessionId: string, text: string): void {
  clearTimeout(noticeTimers.get(sessionId));
  notices.set(sessionId, text);
  noticeTimers.set(
    sessionId,
    setTimeout(() => {
      noticeTimers.delete(sessionId);
      if (notices.delete(sessionId)) emit();
    }, NOTICE_MS),
  );
  emit();
}

function subscribe(notify: () => void): () => void {
  listeners.add(notify);
  return () => {
    listeners.delete(notify);
  };
}

/** The pending comments of a conversation, live: a comment written in one place appears in the
 *  others at once, and the array is the same object until something changes. */
export function useLineComments(sessionId: string | null): readonly LineComment[] {
  return useSyncExternalStore(
    subscribe,
    () => (sessionId ? load(sessionId) : EMPTY),
    () => EMPTY,
  );
}

export function useLineCommentsNotice(sessionId: string | null): string | null {
  return useSyncExternalStore(
    subscribe,
    () => (sessionId ? (notices.get(sessionId) ?? null) : null),
    () => null,
  );
}

/** Asks the conversation that owns `sessionId` to send its pending comments. The diff can be drawn
 *  by a panel that does not own the composer, so the request travels as an event. */
export function requestSendLineComments(sessionId: string): void {
  window.dispatchEvent(new CustomEvent<{ sessionId: string }>(EVENT, { detail: { sessionId } }));
}

/** Runs `send` when the conversation's comments are asked to go out. The latest `send` is used, so
 *  the closure never sees a stale draft or busy flag. */
export function useSendLineCommentsRequest(sessionId: string, send: () => void): void {
  const latest = useRef(send);
  latest.current = send;
  useEffect(() => {
    const onRequest = (event: Event) => {
      const detail = (event as CustomEvent<{ sessionId: string }>).detail;
      if (detail?.sessionId === sessionId) latest.current();
    };
    window.addEventListener(EVENT, onRequest);
    return () => window.removeEventListener(EVENT, onRequest);
  }, [sessionId]);
}
