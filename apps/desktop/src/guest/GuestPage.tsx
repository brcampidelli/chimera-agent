import { Loader2, Send, Users } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";

import { BrandMark } from "@/components/BrandMark";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import {
  getGuestSession,
  GuestError,
  sendGuestTurn,
  streamGuestLive,
  tokenFromLocation,
  type GuestExchange,
  type GuestLiveFrame,
} from "@/guest/api";

/**
 * What a guest sees: one conversation, live, and a box to speak into it.
 *
 * Deliberately not the app. The app is the owner's — projects, settings, the file tree, the
 * governance cards — and none of it belongs on a screen opened by a share token. This page knows
 * the conversation it was given, who else is in it, and how to send a message; when the agent
 * stops to ask the owner something, the guest is told they are waiting on the owner, because
 * only the owner can answer.
 *
 * The name is a label the guest gives, remembered in this browser as a convenience and shown as
 * given to everyone else. It is not an identity, and the presence line does not pretend it is.
 */

interface Row extends GuestExchange {
  author?: string;
  turnId?: string;
  failed?: boolean;
}

// The name the owner's own live window subscribes under (`streamSessionLive`'s default); it is
// the one name in the presence list that is not a person's choice.
const OWNER = "owner";
const NAME_KEY = "chimera.guest.name";

function rememberedName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

export function GuestPage() {
  const t = useT();
  const token = tokenFromLocation();
  const [name, setName] = useState(rememberedName);
  const [joined, setJoined] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [folder, setFolder] = useState("");
  const [presence, setPresence] = useState<string[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [waitingOwner, setWaitingOwner] = useState(false);
  const [gone, setGone] = useState<string | null>(null);
  const [reconnecting, setReconnecting] = useState(false);
  const seq = useRef(0);
  const bottom = useRef<HTMLDivElement | null>(null);

  const apply = useCallback((frame: GuestLiveFrame) => {
    if (frame.session_seq > seq.current) seq.current = frame.session_seq;
    const data = frame.payload;
    if (frame.event === "presence") {
      setPresence((data.names as string[] | undefined) ?? []);
      return;
    }
    if (!frame.turn_id) return;
    const id = frame.turn_id;
    const patch = (fn: (r: Row) => Row) => setRows((prev) => prev.map((r) => (r.turnId === id ? fn(r) : r)));
    switch (frame.event) {
      case "turn_started":
        setWaitingOwner(false);
        setRows((prev) =>
          prev.some((r) => r.turnId === id)
            ? prev
            : [...prev, { you: String(data.message ?? ""), author: frame.author || undefined, turnId: id, answer: "", tools: [], edits: [], done: null }],
        );
        break;
      case "token":
        patch((r) => ({ ...r, answer: r.answer + String(data.text ?? "") }));
        break;
      case "tool":
        patch((r) => ({ ...r, tools: [...r.tools, { name: String(data.name ?? ""), ok: data.ok !== false }] }));
        break;
      case "approval":
        setWaitingOwner(true);
        break;
      case "done":
        setWaitingOwner(false);
        patch((r) => ({ ...r, answer: String(data.answer ?? "") || r.answer, done: data }));
        break;
      case "error":
        setWaitingOwner(false);
        patch((r) => ({ ...r, failed: true }));
        break;
      default:
        break;
    }
  }, []);

  // Load the conversation once joined, then follow it; reconnect on a cut.
  useEffect(() => {
    if (!joined || !token) return;
    const controller = new AbortController();
    let alive = true;
    void (async () => {
      try {
        const session = await getGuestSession(token);
        if (!alive) return;
        setFolder(session.workspace_name);
        setPresence(session.presence);
        setRows(session.exchanges.map((e) => ({ ...e, author: (e.done?.author as string | undefined) || undefined })));
        seq.current = session.seq;
      } catch (err) {
        if (alive) setGone(err instanceof GuestError && err.status === 401 ? t("code.share.guest.gone") : String(err));
        return;
      }
      while (alive) {
        setReconnecting(false);
        let cut: string | null;
        try {
          cut = await streamGuestLive(token, seq.current, name, apply, controller.signal);
        } catch (err) {
          if (err instanceof GuestError && err.status === 401) {
            if (alive) setGone(t("code.share.guest.gone"));
            return;
          }
          cut = String(err);
        }
        if (!alive || cut === null) break;
        setReconnecting(true);
        await new Promise((resolve) => setTimeout(resolve, 3000));
      }
    })();
    return () => {
      alive = false;
      controller.abort();
    };
  }, [joined, token, name, apply, t]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [rows.length, rows[rows.length - 1]?.answer.length]);

  async function send() {
    const message = draft.trim();
    if (!message || sending) return;
    setSending(true);
    setDraft("");
    try {
      await sendGuestTurn(token, message, name);
    } catch (err) {
      setGone(err instanceof GuestError && err.status === 401 ? t("code.share.guest.gone") : String(err));
    } finally {
      setSending(false);
    }
  }

  if (!token || gone) {
    return (
      <main className="mx-auto max-w-2xl p-6 text-sm text-foreground">
        <BrandMark className="h-10 w-10" glow />
        <p className="mt-4 text-bad-foreground" role="alert" data-testid="guest-gone">
          {gone ?? t("code.share.guest.gone")}
        </p>
      </main>
    );
  }

  if (!joined) {
    return (
      <main className="mx-auto max-w-md p-6 text-sm text-foreground">
        <BrandMark className="h-10 w-10" glow />
        <h1 className="mt-4 text-base font-semibold">{t("code.share.guest.title")}</h1>
        <form
          className="mt-3 flex gap-2"
          onSubmit={(ev) => {
            ev.preventDefault();
            const clean = name.trim().slice(0, 40);
            setName(clean);
            try {
              localStorage.setItem(NAME_KEY, clean);
            } catch {
              // a browser that will not remember is still a browser
            }
            setJoined(true);
          }}
        >
          <input
            className="min-w-0 flex-1 rounded-chip border border-border bg-surface px-2 py-1.5 text-sm"
            aria-label={t("code.share.guest.name")}
            placeholder={t("code.share.guest.name")}
            value={name}
            onChange={(ev) => setName(ev.target.value)}
            autoFocus
          />
          <Button type="submit" size="sm">
            {t("code.share.guest.join")}
          </Button>
        </form>
      </main>
    );
  }

  const others = presence.filter((n) => n !== name).map((n) => (n === OWNER ? t("code.share.guest.owner") : n));
  return (
    <main className="mx-auto flex h-screen max-w-3xl flex-col p-4 text-sm text-foreground">
      <header className="flex flex-wrap items-center gap-2 border-b border-hairline pb-2">
        <BrandMark className="h-6 w-6" aria-hidden alt="" />
        <span className="font-semibold">{t("code.share.guest.title")}</span>
        {folder ? <span className="text-xs text-muted-foreground">{t("code.share.guest.folder", { name: folder })}</span> : null}
        <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground" data-testid="guest-presence">
          <Users className="h-3.5 w-3.5" />
          {others.length ? t("code.share.here", { names: others.join(", ") }) : t("code.share.nobody")}
        </span>
      </header>

      <section className="flex-1 space-y-3 overflow-y-auto py-3" data-testid="guest-rows">
        {rows.map((row, i) => (
          <div key={row.turnId ?? i} className="space-y-1.5">
            <div className="rounded-chip bg-surface-2 px-2.5 py-1.5">
              <span className="mr-1.5 rounded-chip bg-accent/15 px-1.5 text-xs font-medium text-accent-ink" data-testid="guest-author">
                {row.author ?? t("code.share.guest.owner")}
              </span>
              {row.you}
            </div>
            {row.tools.length ? (
              <p className="text-xs text-muted-foreground">{row.tools.map((tool) => tool.name).join(" · ")}</p>
            ) : null}
            {row.answer ? (
              <div className="prose-chimera px-1">
                <Markdown>{row.answer}</Markdown>
              </div>
            ) : row.done || row.failed ? null : (
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> {t("code.share.guest.thinking")}
              </p>
            )}
          </div>
        ))}
        <div ref={bottom} />
      </section>

      {waitingOwner ? (
        <p className="text-xs text-warn-foreground" role="status" data-testid="guest-waiting">
          {t("code.share.guest.waitingOwner")}
        </p>
      ) : null}
      {reconnecting ? (
        <p className="text-xs text-muted-foreground" role="status">
          {t("code.share.guest.reconnecting")}
        </p>
      ) : null}

      <form
        className="flex gap-2 border-t border-hairline pt-2"
        onSubmit={(ev) => {
          ev.preventDefault();
          void send();
        }}
      >
        <input
          className="min-w-0 flex-1 rounded-chip border border-border bg-surface px-2 py-1.5 text-sm"
          aria-label={t("code.share.guest.placeholder")}
          placeholder={t("code.share.guest.placeholder")}
          value={draft}
          onChange={(ev) => setDraft(ev.target.value)}
          disabled={sending}
        />
        <Button type="submit" size="sm" disabled={sending || !draft.trim()}>
          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {t("code.share.guest.send")}
        </Button>
      </form>
    </main>
  );
}
