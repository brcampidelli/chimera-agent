import { useCallback, useEffect, useRef, useState , type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Eraser, Loader2, MessageSquare, Send, ShieldCheck, Undo2, Wrench } from "lucide-react";
import {
  deleteCodeSession,
  getCodeSession,
  revertCodeTurn,
  streamCodeTurn,
  type Approval,
  type CodeToolEvent,
  type CodeTurnDone,
  type CodeVerified,
  type Profile,
  type Reach,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { BatchProposal } from "@/components/code/BatchProposal";
import { DiffView } from "@/components/code/DiffView";
import { decompose } from "@/lib/decompose";
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
  /** The verdict on what this turn WROTE. Absent when the turn wrote nothing. */
  verified?: CodeVerified;
  /** Set once the offered undo was taken (or refused by the server) — the offer is single-use. */
  undone?: "ok" | "gone";
}

/** What happened to the files after the turn finished writing them.
 *
 *  This is the sentence that made one button honest. Before it, Send edited your workspace and kept
 *  whatever it wrote, while a second button next to it ran the same text through plan → verify →
 *  revert. Nothing on the screen said which one you were pressing, so the default was silently the
 *  weaker one. Now the default checks, and says what checked it.
 *
 *  The undo is OFFERED. A run reverts itself because the user asked for a verdict; a conversation is
 *  a conversation, and silently undoing what someone watched being typed is a worse surprise than a
 *  failing test. The other offer — hand the same text to the autonomous run, which retries — is how
 *  the multi-attempt path is reached now: as a consequence of a failure, not as a second button
 *  asking the user to guess in advance which kind of work this was. */
function Verdict({
  v,
  undone,
  onUndo,
  onFix,
  t,
}: {
  v: CodeVerified;
  undone?: "ok" | "gone";
  onUndo: () => void;
  onFix: () => void;
  t: TFunc;
}) {
  if (v.state === "none") return <p className="text-xs text-warn">{t("code.chat.verdict.none")}</p>;
  const cmd = v.command ?? "";
  const args = { cmd, src: v.source };
  if (v.state === "abstained")
    return <p className="text-xs text-warn">{t("code.chat.verdict.abstained", args)}</p>;
  if (v.state === "passed")
    return <p className="text-xs text-ok">{t("code.chat.verdict.passed", args)}</p>;
  return (
    <div className="space-y-1.5 rounded-chip border border-bad/40 p-2">
      <p className="text-xs text-bad">{t("code.chat.verdict.failed", args)}</p>
      {v.output ? (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs text-muted-foreground">
          {v.output}
        </pre>
      ) : null}
      {undone ? (
        <p className={cn("text-xs", undone === "ok" ? "text-ok" : "text-bad")}>
          {t(undone === "ok" ? "code.chat.verdict.reverted" : "code.chat.verdict.revertFailed")}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {v.revert_token ? (
            <Button size="sm" variant="ghost" onClick={onUndo}>
              <Undo2 className="h-3.5 w-3.5" /> {t("code.chat.verdict.revert")}
            </Button>
          ) : null}
          <Button size="sm" variant="ghost" onClick={onFix}>
            <ShieldCheck className="h-3.5 w-3.5" /> {t("code.chat.verdict.fix")}
          </Button>
        </div>
      )}
    </div>
  );
}

/** A tool call, compactly: what ran, on what, and whether it worked.
 *
 *  The observation is deliberately behind a title rather than inline — it is clipped server-side,
 *  and a clipped build log rendered as if it were the whole thing is how someone concludes the
 *  tests passed from the first four hundred characters of output. */
function ToolRow({ tool, onOpenFile }: { tool: CodeToolEvent; onOpenFile?: (p: string) => void }) {
  const primary = String(Object.values(tool.arguments)[0] ?? "");
  // A path the agent touched is the handle for looking at it. With the file tree gone this is
  // how the viewer opens — the same way you would click a filename in a terminal that linkifies
  // them. Heuristic on purpose: an argument that looks like a path is offered as one, and one
  // that is not stays plain text rather than becoming a button that does nothing.
  const looksLikePath = /[/\.]/.test(primary) && !primary.includes(" ");
  return (
    <div
      className="flex items-baseline gap-2 font-mono text-xs"
      title={tool.observation || undefined}
    >
      <span className={cn("shrink-0", tool.ok ? "text-ok" : "text-bad")}>{tool.ok ? "✓" : "✗"}</span>
      <span className="shrink-0 text-foreground/80">{tool.name}</span>
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
  onBatch,
  onEdited,
  busyElsewhere,
  posture,
  profile,
  controls,
  onOpenFile,
  resumeSession,
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
  /** The user confirmed a decomposition: run these in parallel, each in its own worktree. */
  onBatch: (tasks: string[]) => void;
  /** A turn changed files — refresh the tree, the viewer and git status. */
  onEdited: () => void;
  /** A run is in flight; sending a turn at the same time would race it in the same workspace. */
  busyElsewhere: boolean;
  /** Rendered just above the input: the settings that govern the next message. */
  controls?: ReactNode;
  /** Open a file the agent touched — the transcript replaced the tree as the way in. */
  onOpenFile?: (path: string) => void;
  /** Continue a stored conversation: its turns are fetched and rendered above the composer. */
  resumeSession?: string | null;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [sessionId, setSessionId] = useState<string | null>(resumeSession ?? null);
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [exchanges]);

  // Load a resumed conversation's turns. Without this the agent silently carried the whole history
  // while the screen showed nothing — the worst combination, because the next question then worked
  // for reasons the user could not see. `replayed` distinguishes "still loading" from "this
  // conversation really is empty", which otherwise render identically and mean opposite things.
  const [replayed, setReplayed] = useState(!resumeSession);
  useEffect(() => {
    if (!resumeSession) return;
    let live = true;
    void getCodeSession(resumeSession)
      .then((session) => {
        if (!live) return;
        setExchanges(session.exchanges.map((e) => ({ ...e, done: null })));
        setReplayed(true);
      })
      .catch(() => {
        // A conversation we cannot read is reported as such rather than shown as empty: "nothing
        // here" and "we failed to fetch it" look the same and are not the same.
        if (live) setReplayed(true);
      });
    return () => {
      live = false;
    };
  }, [resumeSession]);

  const [proposal, setProposal] = useState<string[] | null>(null);

  /** Mutate the turn currently streaming — always the last one, which is the only one that moves. */
  const patchLast = useCallback((fn: (e: Exchange) => Exchange) => {
    setExchanges((prev) =>
      prev.length === 0 ? prev : [...prev.slice(0, -1), fn(prev[prev.length - 1])],
    );
  }, []);

  function send(force = false) {
    const message = draft.trim();
    if (!message || busy || busyElsewhere) return;
    // Several jobs in one message is the request for a parallel batch. It is proposed rather than
    // taken, because worktrees are a real side effect — and `force` is how "send it as one message"
    // gets past this without the proposal reappearing on every keystroke.
    const jobs = force ? [] : decompose(message);
    if (jobs.length >= 2) {
      setProposal(jobs);
      return;
    }
    setProposal(null);
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
        onVerified: (v) => patchLast((e) => ({ ...e, verified: v })),
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

  async function undo(index: number, token: string) {
    let outcome: "ok" | "gone" = "gone";
    try {
      outcome = (await revertCodeTurn(token)).ok ? "ok" : "gone";
    } catch {
      // A failed call and a refused token mean the same thing to the user: the edits are still
      // there. Saying "gone" is the honest read of both, and it is the one that does not imply the
      // files were restored.
    }
    setExchanges((prev) => prev.map((e, j) => (j === index ? { ...e, undone: outcome } : e)));
    if (outcome === "ok") {
      void qc.invalidateQueries({ queryKey: ["fs-file"] });
      void qc.invalidateQueries({ queryKey: ["git-status"] });
      onEdited();
    }
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
        {exchanges.length === 0 && replayed ? (
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
                  <ToolRow key={j} tool={tool} onOpenFile={onOpenFile} />
                ))}
              </div>
            ) : null}
            {e.edits.map((edit, j) => (
              <div key={j} className="space-y-1">
                <button
                  type="button"
                  onClick={() => onOpenFile?.(edit.path)}
                  className="font-mono text-xs text-accent underline decoration-dotted"
                >
                  {edit.path}
                </button>
                <DiffView patch={edit.patch} />
              </div>
            ))}
            {e.answer ? (
              <div className="whitespace-pre-wrap text-sm text-foreground/90">{e.answer}</div>
            ) : null}
            {e.failed ? <p className="text-xs text-bad">{t("code.chat.error")}</p> : null}
            {e.verified ? (
              <Verdict
                v={e.verified}
                undone={e.undone}
                onUndo={() => void undo(i, e.verified?.revert_token ?? "")}
                onFix={() => onHandOff(e.you)}
                t={t}
              />
            ) : null}
            {e.done ? <TurnReceipt done={e.done} t={t} /> : null}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="space-y-2 border-t border-hairline p-3">
        {/* The settings that govern THIS message, next to the box you type it in — the way a model
            selector sits in a composer. They used to be two titled panels stacked above the
            conversation, which is how the conversation ended up with no room: a control that
            describes the next turn does not need more vertical space than the turn itself. */}
        {proposal ? (
          <BatchProposal
            tasks={proposal}
            workspace={workspace}
            onConfirm={() => {
              onBatch(proposal);
              setProposal(null);
              setDraft("");
            }}
            onDecline={() => send(true)}
          />
        ) : null}
        {controls ? <div className="pb-1">{controls}</div> : null}
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
          <Button size="sm" disabled={!draft.trim() || busy || busyElsewhere} onClick={() => send()}>
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
          <span className="ml-auto text-xs text-muted-foreground">{t("code.chat.hint")}</span>
        </div>
      </div>
    </div>
  );
}
