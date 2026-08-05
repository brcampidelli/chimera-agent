import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Eraser, Loader2, MessageSquare, Send, ShieldCheck, Wrench } from "lucide-react";
import {
  deleteCodeSession,
  streamCodeTurn,
  type Approval,
  type CodeToolEvent,
  type CodeTurnDone,
  type Profile,
  type Reach,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { DiffView } from "@/components/code/DiffView";
import { useT, type TFunc } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** One exchange. The assistant side carries what the agent DID (tools, edits) alongside what it
 *  said, because in a coding conversation the tool calls are the substance and the prose is the
 *  caption — a transcript that shows only the prose is a transcript of the wrong half. */
interface Exchange {
  you: string;
  answer: string;
  tools: CodeToolEvent[];
  edits: { path: string; patch: string }[];
  done: CodeTurnDone | null;
  failed?: boolean;
}

/** A tool call, compactly: what ran, on what, and whether it worked.
 *
 *  The observation is deliberately behind a title rather than inline — it is clipped server-side,
 *  and a clipped build log rendered as if it were the whole thing is how someone concludes the
 *  tests passed from the first four hundred characters of output. */
function ToolRow({ tool }: { tool: CodeToolEvent }) {
  const primary = Object.values(tool.arguments)[0] ?? "";
  return (
    <div
      className="flex items-baseline gap-2 font-mono text-xs"
      title={tool.observation || undefined}
    >
      <span className={cn("shrink-0", tool.ok ? "text-ok" : "text-bad")}>{tool.ok ? "✓" : "✗"}</span>
      <span className="shrink-0 text-foreground/80">{tool.name}</span>
      <span className="truncate text-muted-foreground">{primary}</span>
    </div>
  );
}

/** The turn's receipt: steps, the model that answered, the peak prompt, and the cost.
 *
 *  `usd` is null when the model's price is unknown, and this renders that as "price unknown" rather
 *  than as 0 or as nothing — the backend never guesses a price and neither does the UI. */
function TurnReceipt({ done, t }: { done: CodeTurnDone; t: TFunc }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge>{t("code.chat.steps", { n: done.steps })}</Badge>
      {done.model ? <Badge>{done.model}</Badge> : null}
      {done.context_peak_tokens > 0 ? (
        <Badge>{t("code.chat.peak", { n: done.context_peak_tokens.toLocaleString() })}</Badge>
      ) : null}
      <Badge tone={done.usd === null ? "warn" : undefined}>
        {done.usd === null ? t("code.chat.unknownCost") : `$${done.usd.toFixed(4)}`}
      </Badge>
    </div>
  );
}

/** A coding conversation over one workspace: turns that read and edit, keeping their tool calls.
 *
 *  The composer has two buttons and they are not two modes of the same thing. **Send** is a turn:
 *  fast, one model, no verification, and it remembers the previous turns. **Run with verification**
 *  hands the same text to the autonomous run — plan, edit, verify, revert if it fails. The
 *  conversation is the default because most of what happens in a codebase is questions and small
 *  edits; the run is what you press when the change is worth a receipt.
 */
export function Conversation({
  workspace,
  openFile,
  onHandOff,
  onEdited,
  busyElsewhere,
  posture,
  profile,
}: {
  workspace: string;
  openFile: string | null;
  /** The same reach/approval the verified run uses — pressing one button rather than the other must
   *  not change what the agent is allowed to do. */
  posture: { reach: Reach; approval: Approval };
  /** Which tier each role draws from — the same profile the verified run uses. */
  profile: Profile;
  /** Start a verified run with this text, in the panel that owns the run machinery. */
  onHandOff: (text: string) => void;
  /** A turn changed files — refresh the tree, the viewer and git status. */
  onEdited: () => void;
  /** A run is in flight; sending a turn at the same time would race it in the same workspace. */
  busyElsewhere: boolean;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [exchanges]);

  /** Mutate the turn currently streaming — always the last one, which is the only one that moves. */
  const patchLast = useCallback((fn: (e: Exchange) => Exchange) => {
    setExchanges((prev) =>
      prev.length === 0 ? prev : [...prev.slice(0, -1), fn(prev[prev.length - 1])],
    );
  }, []);

  function send() {
    const message = draft.trim();
    if (!message || busy || busyElsewhere) return;
    setDraft("");
    setBusy(true);
    setExchanges((prev) => [...prev, { you: message, answer: "", tools: [], edits: [], done: null }]);
    let touchedFiles = false;

    void streamCodeTurn(
      {
        message,
        session_id: sessionId,
        workspace: workspace || null,
        open_file: openFile,
        posture,
        profile,
      },
      {
        // Sent on every turn, not just the first: a client that drops it silently restarts the
        // conversation, and the symptom is only that the agent seems forgetful.
        onSession: (id) => setSessionId(id),
        onToken: (text) => patchLast((e) => ({ ...e, answer: e.answer + text })),
        onTool: (tool) => patchLast((e) => ({ ...e, tools: [...e.tools, tool] })),
        onEdit: (path, patch) => {
          touchedFiles = true;
          patchLast((e) => ({ ...e, edits: [...e.edits, { path, patch }] }));
        },
        onDone: (done) => {
          // The streamed tokens and the final answer are the same text; prefer the final one, which
          // is complete even when the backend never streamed (a non-streaming model, `stream:false`).
          patchLast((e) => ({ ...e, answer: done.answer || e.answer, done }));
          setBusy(false);
          if (touchedFiles) {
            void qc.invalidateQueries({ queryKey: ["fs-tree"] });
            void qc.invalidateQueries({ queryKey: ["fs-file"] });
            void qc.invalidateQueries({ queryKey: ["git-status"] });
            onEdited();
          }
        },
        onError: () => {
          patchLast((e) => ({ ...e, failed: true }));
          setBusy(false);
        },
      },
    );
  }

  async function clear() {
    const id = sessionId;
    setExchanges([]);
    setSessionId(null);
    if (!id) return;
    try {
      await deleteCodeSession(id);
    } catch {
      // Forgetting the id locally is what the user asked for; a failed server delete leaves an
      // orphan file, which is not worth an error message they can do nothing about.
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-hairline px-3 py-2 text-accent">
        <MessageSquare className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">{t("code.chat.title")}</h2>
        {exchanges.length > 0 ? (
          <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void clear()}>
            <Eraser className="h-3.5 w-3.5" /> {t("code.chat.clear")}
          </Button>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto p-3">
        {exchanges.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t("code.chat.empty")}</p>
        ) : null}
        {exchanges.map((e, i) => (
          <div key={i} className="space-y-2">
            <div className="rounded-chip bg-surface-2 px-2.5 py-1.5 text-sm text-foreground/90">
              {e.you}
            </div>
            {e.tools.length > 0 ? (
              <div className="space-y-1 rounded-chip border border-border p-2">
                <div className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-muted-foreground">
                  <Wrench className="h-3 w-3" /> {t("code.chat.tools")}
                </div>
                {e.tools.map((tool, j) => (
                  <ToolRow key={j} tool={tool} />
                ))}
              </div>
            ) : null}
            {e.edits.map((edit, j) => (
              <div key={j} className="space-y-1">
                <div className="font-mono text-xs text-accent">{edit.path}</div>
                <DiffView patch={edit.patch} />
              </div>
            ))}
            {e.answer ? (
              <div className="whitespace-pre-wrap text-sm text-foreground/90">{e.answer}</div>
            ) : null}
            {e.failed ? <p className="text-xs text-bad">{t("code.chat.error")}</p> : null}
            {e.done ? <TurnReceipt done={e.done} t={t} /> : null}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="space-y-2 border-t border-hairline p-3">
        <textarea
          className="field min-h-[64px] w-full resize-y px-3 py-2 text-sm"
          placeholder={t("code.chat.placeholder")}
          value={draft}
          onChange={(ev) => setDraft(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
              ev.preventDefault();
              send();
            }
          }}
          disabled={busy}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" disabled={!draft.trim() || busy || busyElsewhere} onClick={send}>
            {busy ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> {t("code.chat.sending")}
              </>
            ) : (
              <>
                <Send className="h-4 w-4" /> {t("code.chat.send")}
              </>
            )}
          </Button>
          <span title={t("code.chat.verifiedRunHint")}>
            <Button
              size="sm"
              variant="ghost"
              disabled={!draft.trim() || busy || busyElsewhere}
              onClick={() => onHandOff(draft.trim())}
            >
              <ShieldCheck className="h-4 w-4" /> {t("code.chat.verifiedRun")}
            </Button>
          </span>
          <span className="ml-auto text-xs text-muted-foreground">{t("code.chat.hint")}</span>
        </div>
      </div>
    </div>
  );
}
