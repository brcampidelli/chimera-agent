import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { CornerDownLeft, MessageSquare, Send, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { focusRing } from "@/components/ui/focus";
import { streamSideChat, type SideChatDone } from "@/lib/api";
import { matchesShortcut, shortcutLabel } from "@/lib/hotkeys";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** One question asked beside the conversation, and what came back. Lives only in this panel. */
interface SideExchange {
  id: number;
  question: string;
  answer: string;
  pending: boolean;
  /** The conversation's last turn read untrusted content: the answer is marked, and taking it asks first. */
  tainted?: boolean;
  /** The panel closed while the answer was streaming. The provider may still finish it in the background. */
  stopped?: boolean;
  done?: SideChatDone;
  error?: string;
}

export interface SideChatProps {
  /** The conversation the question is about. A different one starts with an empty panel. */
  sessionId: string;
  /** The model this conversation answers with; "" lets the server use the conversation's own. */
  model: string;
  open: boolean;
  onClose: () => void;
  /** The person chose "take to the conversation": the answer becomes a draft, never a sent turn. */
  onTake: (text: string) => void;
  /** An external agent conversation: the side chat answers only native conversations. */
  unavailable: boolean;
}

/**
 * The text that goes into the composer for a side answer. A tainted answer is quoted under its label,
 * so the person reads that it is data quoted from a conversation that read untrusted content, not an
 * instruction they wrote themselves.
 */
function quoteAnswer(label: string, answer: string): string {
  return `${label}:\n${answer
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n")}`;
}

/**
 * The side question: a panel beside the conversation for a quick question about it.
 *
 * It asks the server, which reads the conversation's transcript and answers with no tools. Nothing
 * asked or answered here goes into the conversation; the only way an answer gets there is the
 * person pressing "take to the conversation", which fills the composer. It stays mounted while
 * closed, so a question survives closing the panel and opening it again. Closing it stops reading
 * the answer; the provider may finish a call already sent, and the panel says so.
 */
export function SideChat({ sessionId, model, open, onClose, onTake, unavailable }: SideChatProps) {
  const t = useT();
  const [exchanges, setExchanges] = useState<SideExchange[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);
  const sequence = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const pendingId = useRef<number | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  function patch(id: number, change: (e: SideExchange) => SideExchange) {
    setExchanges((items) => items.map((e) => (e.id === id ? change(e) : e)));
  }

  /** Stop reading the current answer. The server stops at its next token; a call already sent to the
   *  provider is not recalled, so the exchange says it may finish in the background. */
  function stopCurrent() {
    const current = controller.current;
    if (!current) return;
    controller.current = null;
    current.abort();
    const id = pendingId.current;
    pendingId.current = null;
    if (id !== null) patch(id, (e) => ({ ...e, pending: false, stopped: true }));
    setBusy(false);
  }

  // A question belongs to one conversation: switching conversations abandons the stream and empties
  // the panel, so an answer about one conversation is never shown under another.
  useEffect(() => {
    stopCurrent();
    setExchanges([]);
    setConfirming(null);
  }, [sessionId]);

  // Closing the panel stops the answer being read.
  useEffect(() => {
    if (!open) stopCurrent();
  }, [open]);

  useEffect(() => () => controller.current?.abort(), []);

  useEffect(() => {
    if (open && !unavailable) inputRef.current?.focus();
  }, [open, unavailable]);

  async function ask() {
    const question = draft.trim();
    if (!question || busy || unavailable) return;
    const id = ++sequence.current;
    const abort = new AbortController();
    controller.current = abort;
    pendingId.current = id;
    setExchanges((items) => [...items, { id, question, answer: "", pending: true }]);
    setDraft("");
    setBusy(true);
    await streamSideChat(
      sessionId,
      { message: question, model: model || undefined },
      {
        onStart: (start) => patch(id, (e) => ({ ...e, tainted: start.tainted })),
        onToken: (text) => patch(id, (e) => ({ ...e, answer: e.answer + text })),
        onDone: (done) => patch(id, (e) => ({ ...e, pending: false, done })),
        onError: (message) =>
          patch(id, (e) => ({ ...e, pending: false, error: message || t("sideChat.failed") })),
      },
      abort.signal,
    );
    // Stopped by the panel closing: `stopCurrent` already settled the exchange and the busy state.
    if (abort.signal.aborted) return;
    if (controller.current === abort) controller.current = null;
    if (pendingId.current === id) pendingId.current = null;
    // A stream that ends with neither `done` nor `error` (a cut connection) is not left spinning.
    patch(id, (e) => (e.pending ? { ...e, pending: false, error: e.error ?? t("sideChat.failed") } : e));
    setBusy(false);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void ask();
    }
  }

  function take(exchange: SideExchange) {
    // A tainted answer is not handed over on one press: the person confirms that they want it in the
    // composer, quoted and labelled as data.
    if (exchange.tainted) {
      setConfirming(exchange.id);
      return;
    }
    onTake(exchange.answer);
  }

  function confirmTake(exchange: SideExchange) {
    setConfirming(null);
    onTake(quoteAnswer(t("sideChat.quoteLabel"), exchange.answer));
  }

  function receipt(done: SideChatDone): string {
    const tokens = done.prompt_tokens + done.completion_tokens;
    const cost = done.usd === null ? t("sideChat.costUnknown") : `$${done.usd.toFixed(4)}`;
    return t("sideChat.receipt", { model: done.model, tokens, cost });
  }

  // Closed, the panel is not in the page at all: its field and log are not in the accessibility tree
  // or the tab order. The state above lives on, so reopening shows the same questions.
  if (!open) return null;
  return (
    <aside
      aria-label={t("sideChat.title")}
      data-testid="side-chat"
      className="floating absolute inset-y-0 right-0 z-20 flex w-full max-w-md flex-col"
    >
      <header className="flex items-center gap-2 border-b border-hairline px-3 py-2">
        <MessageSquare className="h-4 w-4 text-accent" aria-hidden />
        <h2 className="min-w-0 flex-1 text-sm font-semibold text-foreground">{t("sideChat.title")}</h2>
        <kbd className="text-xs text-muted-foreground">{shortcutLabel("sideChat")}</kbd>
        <Button variant="ghost" size="sm" aria-label={t("sideChat.close")} onClick={onClose} className={focusRing}>
          <X className="h-4 w-4" />
        </Button>
      </header>
      <p className="border-b border-hairline px-3 py-2 text-xs text-muted-foreground">{t("sideChat.intro")}</p>

      {unavailable ? (
        <p className="px-3 py-3 text-sm text-muted-foreground">{t("sideChat.unavailable")}</p>
      ) : (
        <div role="log" className="min-h-0 flex-1 space-y-4 overflow-auto p-3">
          {exchanges.map((exchange) => (
            <div key={exchange.id} className="space-y-2">
              <p className="rounded-chip bg-surface-2 px-2.5 py-1.5 text-sm text-foreground">{exchange.question}</p>
              {exchange.tainted ? <p className="text-xs text-warn-foreground">{t("sideChat.taintedNote")}</p> : null}
              {exchange.answer ? (
                <p className="whitespace-pre-wrap text-base text-foreground">{exchange.answer}</p>
              ) : null}
              {exchange.pending && !exchange.answer ? (
                <p className="text-xs text-muted-foreground">{t("sideChat.thinking")}</p>
              ) : null}
              {exchange.stopped ? <p className="text-xs text-muted-foreground">{t("sideChat.stoppedNote")}</p> : null}
              {exchange.error ? <p className="text-xs text-warn-foreground">{exchange.error}</p> : null}
              {exchange.done ? <p className="text-xs text-muted-foreground">{receipt(exchange.done)}</p> : null}
              {exchange.answer && !exchange.pending ? (
                confirming === exchange.id ? (
                  <div className="space-y-1.5 rounded-chip border border-hairline p-2">
                    <p className="text-xs text-foreground">{t("sideChat.confirmTake")}</p>
                    <div className="flex gap-2">
                      <Button size="sm" className={focusRing} onClick={() => confirmTake(exchange)}>
                        {t("sideChat.confirmTakeYes")}
                      </Button>
                      <Button variant="ghost" size="sm" className={focusRing} onClick={() => setConfirming(null)}>
                        {t("sideChat.confirmTakeNo")}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <Button variant="ghost" size="sm" className={focusRing} onClick={() => take(exchange)}>
                    <CornerDownLeft className="h-4 w-4" /> {t("sideChat.take")}
                  </Button>
                )
              ) : null}
            </div>
          ))}
        </div>
      )}

      {unavailable ? null : (
        <div className="space-y-2 border-t border-hairline p-3">
          <textarea
            ref={inputRef}
            aria-label={t("sideChat.placeholder")}
            placeholder={t("sideChat.placeholder")}
            value={draft}
            rows={2}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            className={cn("w-full resize-none rounded-lg border border-hairline bg-input px-2.5 py-2 text-sm shadow-inset", focusRing)}
          />
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{t("sideChat.hint")}</span>
            <Button size="sm" disabled={!draft.trim() || busy} onClick={() => void ask()} className={focusRing}>
              <Send className="h-4 w-4" /> {t("sideChat.ask")}
            </Button>
          </div>
        </div>
      )}
    </aside>
  );
}

/** Mod+; opens and closes the side question. Installed by the conversation, so it exists only on the
 *  Code screen; it is allowed while typing, because it opens a panel and changes no draft. */
export function useSideChatKey(toggle: () => void): void {
  const ref = useRef(toggle);
  ref.current = toggle;
  useEffect(() => {
    function handler(e: globalThis.KeyboardEvent) {
      if (!matchesShortcut(e, "sideChat")) return;
      e.preventDefault();
      ref.current();
    }
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
}
