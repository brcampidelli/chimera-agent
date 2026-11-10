import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, X } from "lucide-react";

import {
  ApiError,
  closeTerminal,
  getTerminalStatus,
  openTerminal,
  resizeTerminal,
  sendTerminalInput,
  streamTerminal,
} from "@/lib/api";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The terminal of the Code screen: a REAL shell (a PTY on the server), not the Runner.
 *
 * `cd` and `export` stick, `python` and `vim` and `ssh` work, and the window resizes. It opens in the
 * conversation's own folder — its worktree when it has one — and only where commands are on: the
 * server refuses otherwise, and this panel says so before asking.
 *
 * **Tabs stay mounted while hidden.** Hiding the panel (Ctrl+`) or switching tab must not kill a
 * `npm run dev`; what ends a shell is closing its tab, leaving the conversation (the parent keys this
 * component by it), or the app going away (the output stream ends, and the server closes the shell
 * with it).
 *
 * **xterm is loaded on first use**, as its own chunk: a person who never opens a terminal does not
 * download one. Colours are read from the design tokens, never written here.
 *
 * Keystrokes are batched for a few milliseconds and sent in order, one request at a time: the
 * transport is a POST per burst, and two in flight at once could arrive swapped.
 */

/** Tabs at once. The server enforces the same number; the button greys out here first. */
export const MAX_TABS = 8;
const FLUSH_MS = 4;
/** Dragging a window edge fires a resize per frame; each one would be a fit and a request. */
const RESIZE_MS = 100;

/** A link the shell printed (an OSC 8 hyperlink) is opened only if it is web, and only if asked. */
export function isWebLink(text: string): boolean {
  try {
    const url = new URL(text);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

type Phase = "starting" | "live" | "ended" | "failed";

function token(name: string): string | undefined {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || undefined;
}

function terminalTheme() {
  return {
    background: token("--color-background"),
    foreground: token("--color-foreground"),
    cursor: token("--color-accent"),
    selectionBackground: token("--color-surface-hover"),
    red: token("--color-bad"),
    green: token("--color-ok"),
    yellow: token("--color-warn"),
    blue: token("--color-accent"),
    brightBlack: token("--color-muted-foreground"),
  };
}

function TerminalView({
  workspace,
  active,
  onPhase,
}: {
  workspace: string | null;
  active: boolean;
  onPhase: (phase: Phase) => void;
}) {
  const t = useT();
  const host = useRef<HTMLDivElement | null>(null);
  const fit = useRef<(() => void) | null>(null);
  const [message, setMessage] = useState("");
  const phase = useRef<(p: Phase) => void>(onPhase);
  phase.current = onPhase;

  useEffect(() => {
    let disposed = false;
    let serverId = "";
    const abort = new AbortController();
    let cleanup: () => void = () => {};

    void (async () => {
      const [{ Terminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
        import("@xterm/xterm/css/xterm.css"),
      ]);
      if (disposed || !host.current) return;
      const term = new Terminal({
        fontFamily: token("--font-mono") ?? "monospace",
        fontSize: 13,
        // A person who asked for no motion gets a steady cursor.
        cursorBlink: !(typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches),
        scrollback: 5000,
        theme: terminalTheme(),
        // What a program prints can say "this text links to X". `file:`, `javascript:` and the app's
        // own schemes are never followed, and a web link is opened only after the person confirms
        // the address, since the text shown for it is whatever the program chose.
        linkHandler: {
          allowNonHttpProtocols: false,
          activate: (_event: MouseEvent, text: string) => {
            if (!isWebLink(text)) return;
            if (window.confirm(t("terminal.openLink", { url: text }))) {
              window.open(text, "_blank", "noopener,noreferrer");
            }
          },
        },
      });
      const fitter = new FitAddon();
      term.loadAddon(fitter);
      term.open(host.current);
      // Ctrl+` belongs to the app (it toggles the panel); every other key belongs to the shell.
      term.attachCustomKeyEventHandler((event) => !(event.ctrlKey && event.code === "Backquote"));
      const doFit = () => {
        try {
          fitter.fit();
        } catch {
          // Hidden or not yet laid out: there is nothing to fit, and the next resize will.
        }
      };
      fit.current = doFit;
      doFit();

      let pending = "";
      let timer: ReturnType<typeof setTimeout> | null = null;
      let chain: Promise<unknown> = Promise.resolve();
      const flush = () => {
        timer = null;
        const chunk = pending;
        pending = "";
        if (!chunk || !serverId) return;
        const id = serverId;
        chain = chain
          .then(() => sendTerminalInput(id, chunk))
          // A refusal (commands turned off since the shell opened) must be seen, not swallowed.
          .catch((err: unknown) => {
            if (err instanceof ApiError && err.status === 403) setMessage(err.message);
          });
      };
      term.onData((data) => {
        pending += data;
        if (timer === null) timer = setTimeout(flush, FLUSH_MS);
      });
      let resizeTimer: ReturnType<typeof setTimeout> | null = null;
      term.onResize(({ cols, rows }) => {
        if (resizeTimer !== null) clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
          resizeTimer = null;
          if (serverId) void resizeTerminal(serverId, cols, rows).catch(() => {});
        }, RESIZE_MS);
      });

      cleanup = () => {
        if (timer !== null) clearTimeout(timer);
        if (resizeTimer !== null) clearTimeout(resizeTimer);
        term.dispose();
      };

      try {
        const opened = await openTerminal(workspace, term.cols, term.rows);
        serverId = opened.id;
      } catch (err) {
        const reason = err instanceof ApiError ? err.message : err instanceof Error ? err.message : "";
        setMessage(t("terminal.failed", { reason }));
        phase.current("failed");
        return;
      }
      if (disposed) {
        void closeTerminal(serverId).catch(() => {});
        return;
      }
      phase.current("live");
      term.focus();
      await streamTerminal(
        serverId,
        {
          onData: (text) => term.write(text),
          onExit: (code) => {
            term.write(`\r\n${t("terminal.exited", { code: code === null ? "?" : String(code) })}\r\n`);
            phase.current("ended");
          },
          onError: (reason) => {
            setMessage(t("terminal.failed", { reason }));
            phase.current("failed");
          },
        },
        abort.signal,
      );
    })();

    return () => {
      disposed = true;
      // The stream going away ends the shell on the server; the explicit close is for the one that
      // would otherwise wait for its grace, and for a server that did not notice the disconnect.
      abort.abort();
      if (serverId) void closeTerminal(serverId).catch(() => {});
      cleanup();
    };
    // One shell per mount: a changed workspace is a different conversation, and the parent keys on it.
  }, []);

  // Fit when the pane resizes, and when this tab comes to the front (a hidden tab measures zero).
  useEffect(() => {
    const element = host.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const observer = new ResizeObserver(() => {
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => fit.current?.(), RESIZE_MS);
    });
    observer.observe(element);
    return () => {
      if (timer !== null) clearTimeout(timer);
      observer.disconnect();
    };
  }, []);
  useEffect(() => {
    if (active) fit.current?.();
  }, [active]);

  return (
    <div className="relative min-h-0 flex-1" hidden={!active}>
      <div ref={host} className="h-full w-full px-2 py-1" data-testid="terminal-host" />
      {message ? (
        <p role="alert" className="absolute inset-x-2 top-1 rounded-md bg-card px-2 py-1 text-xs text-bad-foreground">
          {message}
        </p>
      ) : null}
    </div>
  );
}

interface Tab {
  key: number;
  n: number;
  phase: Phase;
}

export function TerminalPanel({
  workspace,
  commandsOn,
  visible,
  onHide,
}: {
  /** The folder to open in: the conversation's (its worktree when it has one), else the project's. */
  workspace: string | null;
  /** The folder's commands are on. Off, no shell is asked for. */
  commandsOn: boolean;
  visible: boolean;
  onHide: () => void;
}) {
  const t = useT();
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  // Why this machine cannot host a terminal (Windows without its backend), asked once before the
  // first shell so the reason is a sentence and not a failed request.
  const [unavailable, setUnavailable] = useState("");
  const asked = useRef(false);
  const counter = useRef(0);

  const add = useCallback(() => {
    counter.current += 1;
    const key = counter.current;
    setTabs((prev) => (prev.length >= MAX_TABS ? prev : [...prev, { key, n: key, phase: "starting" }]));
    setCurrent(key);
  }, []);

  // The first time the panel is shown (and commands are on), it opens one tab. Never before: a
  // person who does not open the terminal has no shell started for them.
  useEffect(() => {
    if (!visible || !commandsOn || tabs.length > 0 || unavailable) return;
    if (asked.current) {
      add();
      return;
    }
    asked.current = true;
    void getTerminalStatus()
      .then((status) => (status.available ? add() : setUnavailable(status.reason)))
      // A server that cannot answer the question is asked for the shell anyway: it will say why.
      .catch(() => add());
  }, [visible, commandsOn, tabs.length, unavailable, add]);

  const close = useCallback(
    (key: number) => {
      const next = tabs.filter((tab) => tab.key !== key);
      setTabs(next);
      setCurrent((now) => (now === key ? (next[next.length - 1]?.key ?? null) : now));
      // The last tab closing closes the panel, as in the apps this is modelled on; reopening starts one.
      if (next.length === 0) onHide();
    },
    [tabs, onHide],
  );

  const setPhase = useCallback((key: number, phase: Phase) => {
    setTabs((prev) => prev.map((tab) => (tab.key === key ? { ...tab, phase } : tab)));
  }, []);

  const full = tabs.length >= MAX_TABS;

  return (
    <section
      aria-label={t("terminal.title")}
      hidden={!visible}
      className="flex h-72 shrink-0 flex-col border-t border-hairline bg-background"
    >
      <div className="flex shrink-0 items-center gap-1 border-b border-hairline px-2 py-1">
        <div role="tablist" aria-label={t("terminal.title")} className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
          {tabs.map((tab) => (
            <span
              key={tab.key}
              className={cn(
                "flex items-center rounded-md text-xs",
                current === tab.key ? "bg-surface-hover text-foreground" : "text-muted-foreground",
              )}
            >
              <button
                type="button"
                role="tab"
                aria-selected={current === tab.key}
                onClick={() => setCurrent(tab.key)}
                className={cn("rounded-md px-2 py-1", focusRing)}
              >
                {t("terminal.tab", { n: String(tab.n) })}
                {tab.phase === "ended" ? ` · ${t("terminal.ended")}` : ""}
              </button>
              <button
                type="button"
                aria-label={t("terminal.closeTab", { n: String(tab.n) })}
                onClick={() => close(tab.key)}
                className={cn("rounded-md px-1 py-1 hover:text-foreground", focusRing)}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
        <button
          type="button"
          onClick={add}
          disabled={full || !commandsOn}
          title={full ? t("terminal.limit", { max: String(MAX_TABS) }) : undefined}
          aria-label={t("terminal.newTab")}
          className={cn(
            "rounded-md p-1 text-muted-foreground hover:bg-surface-hover hover:text-foreground disabled:opacity-40",
            focusRing,
          )}
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onHide}
          aria-label={t("terminal.hide")}
          className={cn(
            "rounded-md p-1 text-muted-foreground hover:bg-surface-hover hover:text-foreground",
            focusRing,
          )}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {unavailable ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">{t("terminal.unavailable", { reason: unavailable })}</p>
      ) : commandsOn ? (
        tabs.map((tab) => (
          <TerminalView
            key={tab.key}
            workspace={workspace}
            active={current === tab.key}
            onPhase={(phase) => setPhase(tab.key, phase)}
          />
        ))
      ) : (
        <p className="px-4 py-3 text-xs text-muted-foreground">{t("terminal.commandsOff")}</p>
      )}
    </section>
  );
}
