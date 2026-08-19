import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import Markdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  Copy,
  Download,
  Eraser,
  MessageSquare,
  Network,
  Send,
  ShieldCheck,
  Square,
  Undo2,
  Wrench,
} from "lucide-react";
import {
  deleteCodeSession,
  getCodeSession,
  revertCodeTurn,
  streamCodeTurn,
  type Approval,
  type CodeToolEvent,
  type CodeTurnDone,
  type Attachment,
  type CodeVerified,
  type Profile,
  type Reach,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { BrandMark } from "@/components/BrandMark";
import {
  AttachButton,
  AttachmentTray,
  DictateButton,
  useAttachmentUpload,
} from "@/components/code/Attachments";
import { BatchProposal } from "@/components/code/BatchProposal";
import { DiffView } from "@/components/code/DiffView";
import {
  EMPTY_CAST,
  FusionCast,
  type Cast,
} from "@/components/code/FusionCast";
import { SpendCeiling } from "@/components/code/SpendCeiling";
import { decompose } from "@/lib/decompose";
import {
  exchangeToMarkdown,
  reconcile,
  toMarkdown,
  transcriptFilename,
  type TranscriptExchange,
} from "@/lib/transcript";
import { useT, type TFunc } from "@/lib/i18n";
import { useAgent } from "@/lib/agent-context";
import { useStickToBottom } from "@/lib/useStickToBottom";
import { cn } from "@/lib/utils";

/** Share of the model's window this screen spends on the prompt before compacting.
 *
 *  The library default is OFF, deliberately: compaction discards messages, and an API caller who
 *  never asked for that should not silently get it. A long-running chat is the case where that
 *  default is wrong. Without a budget the message list only grows and an overflow is terminal —
 *  the provider raises, the failover table maps CONTEXT_OVERFLOW to ABORT, and a conversation the
 *  user has been building for an hour ends on a provider error with no way to continue it. Nobody
 *  is standing by to restart a chat window the way an operator restarts a job.
 *
 *  0.6 is the library's own `DEFAULT_BUDGET_FRACTION`, matched on purpose so the app and `chimera
 *  solve --context-budget` behave the same at the same number. Compaction here is free: no
 *  summarising model call, just a structural note plus the recent tail, and it fires at 80% of the
 *  budget so there is still room to compact into. */
const CONTEXT_BUDGET = 0.6;

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
  /** What the server actually said when the turn failed. A wrong API key, a rate limit, a model
   *  that does not exist and a provider outage all look identical without it. */
  error?: string;
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
  if (v.state === "none")
    return <p className="text-xs text-warn">{t("code.chat.verdict.none")}</p>;
  const cmd = v.command ?? "";
  const args = { cmd, src: v.source };
  if (v.state === "abstained")
    return (
      <p className="text-xs text-warn">
        {t("code.chat.verdict.abstained", args)}
      </p>
    );
  if (v.state === "passed")
    return (
      <p className="text-xs text-ok">{t("code.chat.verdict.passed", args)}</p>
    );
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
          {t(
            undone === "ok"
              ? "code.chat.verdict.reverted"
              : "code.chat.verdict.revertFailed",
          )}
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

/** A tool call, compactly: what ran, on what, whether it worked — and what it said.
 *
 *  The observation used to live in a native `title=`, which cannot be selected, cannot be copied,
 *  and is truncated again by the OS at a length we do not control. A failing command's output is
 *  the thing a person most wants to paste somewhere, and it was the one thing they could not take.
 *
 *  It stays FOLDED, and the summary says the output is clipped. The clip is real — the server keeps
 *  the head and the tail of 400 characters — and this deliberately does not offer "full output",
 *  because there is no route on our side that has it. Promising it and then showing the same 400
 *  characters would be worse than the tooltip. */
function ToolRow({
  tool,
  onOpenFile,
}: {
  tool: CodeToolEvent;
  onOpenFile?: (p: string) => void;
}) {
  const t = useT();
  const primary = String(Object.values(tool.arguments)[0] ?? "");
  const [copied, setCopied] = useState(false);
  // A path the agent touched is the handle for looking at it. With the file tree gone this is
  // how the viewer opens — the same way you would click a filename in a terminal that linkifies
  // them. Heuristic on purpose: an argument that looks like a path is offered as one, and one
  // that is not stays plain text rather than becoming a button that does nothing.
  const looksLikePath = /[/\.]/.test(primary) && !primary.includes(" ");
  return (
    <div className="font-mono text-xs">
      <div className="flex items-baseline gap-2">
        <span className={cn("shrink-0", tool.ok ? "text-ok" : "text-bad")}>
          {tool.ok ? "✓" : "✗"}
        </span>
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
      {tool.observation ? (
        <details className="ml-5 mt-0.5" open={!tool.ok}>
          {/* Open by default when the tool FAILED. That is the case where the output is the reason
              the user is reading this screen at all; for a successful `read_file` it is noise. */}
          <summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground">
            {t("code.chat.tool.output")}
          </summary>
          <div className="relative">
            <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-chip bg-surface-2 p-2 pr-16 text-muted-foreground">
              {tool.observation}
            </pre>
            <button
              type="button"
              className="absolute right-1 top-2 rounded-chip bg-surface-1 px-1.5 py-0.5 text-muted-foreground hover:text-foreground"
              onClick={() => {
                // Best-effort: `navigator.clipboard` is absent in a non-secure context and rejects
                // when the window is not focused. Failing silently leaves the button looking
                // broken, so the label only changes once the write actually resolved.
                void navigator.clipboard
                  ?.writeText(tool.observation)
                  .then(() => setCopied(true))
                  .catch(() => setCopied(false));
              }}
            >
              {t(copied ? "code.chat.tool.copied" : "code.chat.tool.copy")}
            </button>
          </div>
        </details>
      ) : null}
    </div>
  );
}

/** The turn's receipt: steps, the model that answered, the peak prompt, and the cost.
 *
 *  `usd` is null when the model's price is unknown, and this renders that as "price unknown" rather
 *  than as 0 or as nothing — the backend never guesses a price and neither does the UI. */
/** Reasons a turn ended that the user needs to know about. `final` is absent on purpose: a turn
 *  that finished needs no badge saying so, and adding one would bury the four that mean the work
 *  is INCOMPLETE among nine that are routine. */
const STOP_REASONS: Record<string, string> = {
  max_steps: "code.chat.stopped.maxSteps",
  tool_loop: "code.chat.stopped.toolLoop",
  budget: "code.chat.stopped.budget",
  cancelled: "code.chat.stopped.cancelled",
};

function TurnReceipt({ done, t }: { done: CodeTurnDone; t: TFunc }) {
  // `stopped_reason` arrives on every `done` frame and, outside types and mocks, nothing read it.
  // So a turn that hit the step ceiling and stopped mid-task was pixel-for-pixel identical to one
  // that finished the work — the receipt drew nine badges and not the one that says whether to
  // believe the other nine.
  const stopped = STOP_REASONS[done.stopped_reason ?? ""];
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {/* First, ahead of every measurement, because it is what decides how to read them. */}
      {stopped ? <Badge tone="warn">{t(stopped)}</Badge> : null}
      {/* Who did the work, first — it is the fact that reframes every badge after it. */}
      {done.external ? (
        <Badge tone="warn">
          {t("code.chat.external", { agent: done.external })}
        </Badge>
      ) : null}
      {/* Null, not zero: an external turn's steps happened inside somebody else's loop and it did
          not report them. Rendering "0 steps" would say it did nothing. */}
      {done.steps !== null ? (
        <Badge>{t("code.chat.steps", { n: done.steps })}</Badge>
      ) : null}
      {done.model && !done.external ? <Badge>{done.model}</Badge> : null}
      {done.context_peak_tokens !== null && done.context_peak_tokens > 0 ? (
        <Badge>
          {t("code.chat.peak", {
            n: done.context_peak_tokens.toLocaleString(),
          })}
        </Badge>
      ) : null}
      {/* Measured inside the model calls, not divided out of the turn's duration — the tools and
          the verifier are not the model, and folding them in reports a shell command as slow
          generation. Absent (never 0) when nothing was timed. */}
      {done.tokens_per_second != null ? (
        <Badge>
          {t("code.chat.speed", { n: Math.round(done.tokens_per_second) })}
        </Badge>
      ) : null}
      {/* Every permission we answered on the user's behalf, and every write the region refused.
          Both are the receipt's half of the bargain the posture note describes. */}
      {done.auto_approved?.length ? (
        <Badge tone="warn">
          {t("code.chat.autoApproved", { n: done.auto_approved.length })}
        </Badge>
      ) : null}
      {done.refused_writes?.length ? (
        <Badge tone="warn">
          {t("code.chat.refusedWrites", { n: done.refused_writes.length })}
        </Badge>
      ) : null}
      <Badge tone={done.usd === null ? "warn" : undefined}>
        {done.usd === null
          ? t("code.chat.unknownCost")
          : `$${done.usd.toFixed(4)}`}
      </Badge>
      {/* Only when a layer actually contributed. "0 facts" and "we did not look" render the same and
          mean different things, so the absent case says nothing rather than saying zero. */}
      {done.memory_facts_used ? (
        <Badge>
          {t("code.chat.recalled", {
            n: done.memory_facts_used,
            layer: done.memory_layer ?? "",
          })}
        </Badge>
      ) : null}
      {done.tainted ? (
        <Badge tone="warn">{t("code.chat.tainted")}</Badge>
      ) : null}
    </div>
  );
}

/** Tell the user their turn finished, when they are not looking at it.
 *
 *  A three-minute run is a reason to go and do something else, and coming back to find it finished
 *  four minutes ago is the whole complaint. No IPC and no plugin: the app is served from 127.0.0.1,
 *  which is a secure context by specification, so the Web Notification API is available on the page.
 *
 *  Only when the window is NOT focused. A notification for something the user is already watching
 *  is the fastest way to have every notification muted, including the one that mattered.
 *
 *  ⚠️ macOS IS UNVERIFIED. WKWebView has historically not implemented `Notification`, and there was
 *  no Mac to test on. Every call is guarded by a capability check and every failure is swallowed, so
 *  the worst case is silence rather than a crash — but "it works on macOS" is NOT a claim being made
 *  here. If it turns out not to, the fix is a native plugin, which is a bigger job than this item.
 */
async function notifyTurnFinished(title: string, body: string): Promise<void> {
  try {
    if (typeof Notification === "undefined") return;
    if (document.visibilityState === "visible" && document.hasFocus()) return;
    // Asked at the moment it is first needed, not on load: a permission prompt that appears before
    // the user has done anything is the one people deny reflexively.
    const permission =
      Notification.permission === "default"
        ? await Notification.requestPermission()
        : Notification.permission;
    if (permission !== "granted") return;
    new Notification(title, { body });
  } catch {
    // A denied permission, a webview without the API, a platform quirk — none of them are worth
    // interrupting a finished turn over.
  }
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
  provider = "",
  model = "",
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
  /** Who does the work: "" for Chimera's own loop, or an external agent key. */
  provider?: string;
  /** Which model answers, for THIS conversation. "" leaves the install's default in charge, which
   *  is what every build before the picker did. Ignored when `provider` names an external agent —
   *  Claude Code and Gemini choose their own, and sending one would describe a routing that did not
   *  happen. */
  model?: string;
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
  const [sessionId, setSessionId] = useState<string | null>(
    resumeSession ?? null,
  );
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // Follows the stream by writing scrollTop once per frame, and stops the moment the reader scrolls
  // up. Replaces a `scrollIntoView` on every state change, which yanked the reader back mid-read —
  // and a coding transcript is read backwards far more often than a chat is.
  const { stuck, scrollToBottom } = useStickToBottom(scrollRef, [exchanges]);
  // The turn in flight, so it can be abandoned. `streamCodeTurn` always accepted a signal; nothing
  // ever passed one, so a turn that went wrong had to be waited out.
  const abortRef = useRef<AbortController | null>(null);
  // The tools of the turn in flight, in a ref because `publish` is not a React state updater and
  // reading the previous value from a closure would drop every call after the first.
  const toolsRef = useRef<{ name: string; ok: boolean }[]>([]);

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
  const [fuse, setFuse] = useState(false);
  // Empty means "whatever this install is configured for", which is what every turn sent before the
  // cast could be chosen at all. Per conversation, like the model and the ceiling beside it.
  const [cast, setCast] = useState<Cast>(EMPTY_CAST);
  /** A dollar ceiling for each turn of this conversation, or null for none (the default, and the
   *  behaviour every earlier build had). Session-local like `provider` rather than persisted: a
   *  standing spend limit is a different promise from "cap this piece of work", and a ceiling that
   *  quietly stayed on from last week would stop a turn for a reason nobody remembers choosing. */
  const [maxUsd, setMaxUsd] = useState<number | null>(null);
  const [attached, setAttached] = useState<Attachment[]>([]);
  // Same upload path the paperclip uses — see `useAttachmentUpload`. The button keeps its own
  // instance for its own spinner; what must not fork is how a file gets uploaded and how a failure
  // is reported.
  const { upload, failed: uploadFailed } = useAttachmentUpload((a) =>
    setAttached((prev) => [...prev, a]),
  );
  const [dragging, setDragging] = useState(false);
  /** Off by default: an app that starts sending desktop notifications without being asked is one
   *  people turn off entirely. Stored per browser profile, which is per install. */
  const [notifyOnFinish, setNotifyOnFinish] = useState(
    () => localStorage.getItem("chimera.notifyOnFinish") === "1",
  );
  const [exportNote, setExportNote] = useState("");
  /** A follow-up typed while a turn was still running. Shown, never silent — a message that
   *  disappeared into a queue nobody can see is indistinguishable from one that was dropped. */
  const [queued, setQueued] = useState<string | null>(null);
  /** Set by `releaseQueued`, consumed by the effect below. The send has to happen AFTER `busy` has
   *  actually gone false, and `setBusy(false)` in the same handler has not landed yet — calling
   *  `send()` there would hit the `busy` branch and re-queue the message it was releasing. */
  const queuedToSendRef = useRef<string | null>(null);
  // Publish what this turn is doing, so the shell's footer and the activity panel keep working from
  // any screen. There is a test that exists precisely to say the agent must stay visible when you
  // navigate away mid-turn.
  const { publish } = useAgent();

  /** Mutate the turn currently streaming — always the last one, which is the only one that moves. */
  const patchLast = useCallback((fn: (e: Exchange) => Exchange) => {
    setExchanges((prev) =>
      prev.length === 0
        ? prev
        : [...prev.slice(0, -1), fn(prev[prev.length - 1])],
    );
  }, []);

  /** What happens to a queued follow-up when the turn it was waiting behind finishes.
   *
   *  Auto-send is right for an ordinary turn: the follow-up is the next thing the user was going to
   *  say, and making them press Enter again for a message they already wrote is the wait this
   *  feature exists to remove.
   *
   *  It is wrong when the turn failed, and this is the part a queue usually gets careless about.
   *  These turns EDIT FILES. On a failed verification the screen offers Undo and Fix, and a queued
   *  message firing into that moment races the user's decision — it would run against a tree they
   *  have not looked at, possibly one they were about to revert. So the text goes back in the box,
   *  where they can read it against what just happened and decide. Nothing is lost either way; what
   *  changes is who decides.
   */
  // Send the released follow-up only once `busy` has actually gone false. Doing it inside `onDone`
  // would run before that `setBusy(false)` had landed, so `send()` would take the busy branch and
  // re-queue the very message it was releasing — a queue that never drains.
  const sendRef = useRef<(force?: boolean, override?: string) => void>(
    () => {},
  );
  useEffect(() => {
    if (busy) return;
    const text = queuedToSendRef.current;
    if (!text) return;
    queuedToSendRef.current = null;
    sendRef.current(false, text);
  }, [busy]);

  /** Write the conversation to a Markdown file the user chooses.
   *
   *  The stored copy is fetched FIRST and reconciled with what is in memory. A session replayed
   *  after a reload, or continued in a second window, can hold turns this component never saw, and
   *  a transcript that is quietly missing the middle is worse than no transcript — it will be
   *  believed. When the two disagree the user is told, rather than the difference being smoothed
   *  over.
   */
  const exportTranscript = useCallback(async () => {
    setExportNote("");
    let stored: TranscriptExchange[] | null = null;
    if (sessionId) {
      try {
        stored = (await getCodeSession(sessionId))
          .exchanges as TranscriptExchange[];
      } catch {
        // A transcript from memory is worth more than none. Say so rather than failing the export.
        setExportNote(t("code.chat.export.storedUnreachable"));
      }
    }
    const { exchanges: rows, recovered } = reconcile(
      exchanges as TranscriptExchange[],
      stored,
    );
    if (recovered > 0)
      setExportNote(t("code.chat.export.recovered", { n: recovered }));

    const exportedAt = new Date().toISOString();
    const markdown = toMarkdown(rows, {
      workspace: workspace || undefined,
      exportedAt,
    });
    const name = transcriptFilename(exportedAt);
    try {
      // A Blob download inside the Tauri webview is the uncertain half of this item, so the failure
      // is caught rather than assumed away: if the anchor click does nothing, the text still reaches
      // the clipboard and the user is told where it went.
      const url = URL.createObjectURL(
        new Blob([markdown], { type: "text/markdown" }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch {
      await navigator.clipboard?.writeText(markdown).catch(() => undefined);
      setExportNote(t("code.chat.export.toClipboard"));
    }
  }, [exchanges, sessionId, workspace, t]);

  const releaseQueued = useCallback((failed: boolean) => {
    setQueued((text) => {
      if (!text) return null;
      if (failed) {
        setDraft((d) => (d ? `${text}\n${d}` : text));
        return null;
      }
      queuedToSendRef.current = text;
      return null;
    });
  }, []);

  function send(force = false, override?: string) {
    // `override` is the queued follow-up being released: it was typed into the box, then moved out
    // of it, so by now `draft` holds whatever was typed AFTER it and reading state here would send
    // the wrong text.
    const message = (override ?? draft).trim();
    if (busyElsewhere) return;
    // Busy is no longer a wall. Losing a thought because a turn is still running is the whole
    // complaint; the message waits, visibly, and goes out when the turn ends.
    if (busy) {
      if (message) {
        setQueued(message);
        setDraft("");
      }
      return;
    }
    if (!message) return;
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
    setAttached([]);
    setBusy(true);
    setExchanges((prev) => [
      ...prev,
      { you: message, answer: "", tools: [], edits: [], done: null },
    ]);
    let touchedFiles = false;
    // Whether THIS turn's verifier failed. It decides what happens to a queued follow-up, and it
    // has to be a local rather than state: `onDone` fires in the same tick as the last `setState`,
    // so reading it from state there would read the previous turn's value.
    let verifyFailed = false;

    const controller = new AbortController();
    abortRef.current = controller;
    toolsRef.current = [];
    publish({
      status: "thinking",
      tools: [],
      report: null,
      busy: true,
      stop: abandon,
    });

    void streamCodeTurn(
      {
        message,
        session_id: sessionId,
        workspace: workspace || null,
        open_file: openFile,
        // See CONTEXT_BUDGET. Sent on every turn because the agent is rebuilt per turn from this
        // request — a budget sent once is a budget that applied once.
        context_budget: CONTEXT_BUDGET,
        // Same reason, and omitted rather than sent null when nothing is armed: the server refuses
        // a non-positive ceiling, so the only two things this field may ever carry are a real
        // ceiling and nothing at all.
        ...(maxUsd === null ? {} : { max_usd: maxUsd }),
        posture,
        profile,
        fuse,
        // Only with `fuse`: a cast on a turn that is not fused would be a second, invisible way to
        // pick a model. Omitted rather than sent empty, so an unchosen role stays the install's.
        ...(fuse && cast.panel.length ? { fusion_panel: cast.panel } : {}),
        ...(fuse && cast.judge ? { fusion_judge: cast.judge } : {}),
        ...(fuse && cast.synthesizer
          ? { fusion_synthesizer: cast.synthesizer }
          : {}),
        // "" means Chimera's own loop, and the field is omitted rather than sent empty — an empty
        // string is a value the server would have to special-case, and a caller that never heard of
        // providers must send exactly what it sent before.
        ...(provider ? { provider } : {}),
        // Same rule, one line later: no model chosen is the field ABSENT, which is what makes the
        // server fall back to `CHIMERA_DEFAULT_MODEL`. Sent per turn because the agent is rebuilt
        // from this request each time — and because the picker is allowed to change mid-conversation,
        // so the receipt under each answer names the model that answered THAT one.
        ...(provider || !model ? {} : { model }),
        attachments: attached.map((a) => a.id),
      },
      {
        // Sent on every turn, not just the first: a client that drops it silently restarts the
        // conversation, and the symptom is only that the agent seems forgetful.
        onSession: (id) => {
          // Invalidate on the FIRST turn's id, not on every turn: the sidebar lists conversations,
          // and a conversation that already exists in the list has not changed by gaining a message.
          if (id !== sessionId)
            void qc.invalidateQueries({ queryKey: ["code-sessions"] });
          setSessionId(id);
        },
        onToken: (text) => {
          publish({ status: "streaming" });
          patchLast((e) => ({ ...e, answer: e.answer + text }));
        },
        onTool: (tool) => {
          publish({
            tools: [...toolsRef.current, { name: tool.name, ok: tool.ok }],
          });
          toolsRef.current = [
            ...toolsRef.current,
            { name: tool.name, ok: tool.ok },
          ];
          patchLast((e) => ({ ...e, tools: [...e.tools, tool] }));
        },
        onEdit: (path, patch) => {
          touchedFiles = true;
          patchLast((e) => ({ ...e, edits: [...e.edits, { path, patch }] }));
        },
        onVerified: (v) => {
          verifyFailed = v.state === "failed";
          patchLast((e) => ({ ...e, verified: v }));
        },
        onDone: (done) => {
          // The streamed tokens and the final answer are the same text; prefer the final one, which
          // is complete even when the backend never streamed (a non-streaming model, `stream:false`).
          patchLast((e) => ({ ...e, answer: done.answer || e.answer, done }));
          // The ceiling rides with the receipt because the bar cannot read it anywhere else: the
          // `done` frame reports what this turn SPENT, and a denominator is a property of what was
          // ASKED for, which only the request knows.
          //
          // It is the ceiling this turn actually ran under, not whatever the box says by the time
          // it finishes: `send` closes over this render's `maxUsd`, so the request above and this
          // line read one value that cannot change under either of them. (The control is disabled
          // while the turn runs as well, which is belt and braces rather than the mechanism.)
          publish({
            status: "done",
            busy: false,
            report: { ...done, max_usd: maxUsd },
          });
          setBusy(false);
          if (notifyOnFinish) {
            void notifyTurnFinished(
              t("code.chat.notify.title"),
              message.slice(0, 120),
            );
          }
          releaseQueued(verifyFailed);
          if (touchedFiles) {
            void qc.invalidateQueries({ queryKey: ["fs-tree"] });
            void qc.invalidateQueries({ queryKey: ["fs-file"] });
            void qc.invalidateQueries({ queryKey: ["git-status"] });
            onEdited();
          }
        },
        // The parameter is the whole fix. This read `onError: () => {…}` — the message arrives
        // (api.ts passes `payload.message`) and was discarded by the signature itself, while
        // Agents.tsx, Tasks.tsx and editor/Runner.tsx in this same app all show it. Not a design
        // choice about noise; an inconsistency nobody noticed.
        onError: (message) => {
          patchLast((e) => ({ ...e, failed: true, error: message }));
          publish({ status: "idle", busy: false });
          setBusy(false);
          // A failure is MORE worth interrupting for than a success: the user walked away expecting
          // work to happen, and it stopped.
          if (notifyOnFinish) {
            void notifyTurnFinished(
              t("code.chat.notify.failed"),
              message.slice(0, 120),
            );
          }
          // A turn that errored is never a base to send the next one from — hand the text back.
          releaseQueued(true);
        },
      },
      controller.signal,
    );
  }

  sendRef.current = send;

  /** Abandon the turn in flight. The model call cannot be un-made, but the stream stops arriving and
   *  the composer comes back — which is the difference between waiting and being stuck. */
  function abandon() {
    abortRef.current?.abort();
    abortRef.current = null;
    publish({ status: "idle", busy: false });
    setBusy(false);
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
    setExchanges((prev) =>
      prev.map((e, j) => (j === index ? { ...e, undone: outcome } : e)),
    );
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
      void qc.invalidateQueries({ queryKey: ["code-sessions"] });
    } catch {
      // Forgetting the id locally is what the user asked for; a failed server delete leaves an
      // orphan file, which is not worth an error message they can do nothing about.
    }
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-hairline px-3 py-2 text-accent">
        <MessageSquare className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">
          {t("code.chat.title")}
        </h2>
        {exchanges.length > 0 ? (
          <div className="ml-auto flex items-center gap-1">
            {/* A record of what an agent did to a repository should be able to leave the window it
                happened in. Until this, the only clipboard call in the whole app copied a `pip
                install` line. */}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => void exportTranscript()}
            >
              <Download className="h-3.5 w-3.5" /> {t("code.chat.export.label")}
            </Button>
            {/* Off by default and remembered per install. An app that starts sending desktop
                notifications unasked is one people turn off entirely, including for the run that
                mattered. */}
            <Button
              size="sm"
              variant={notifyOnFinish ? "primary" : "ghost"}
              aria-pressed={notifyOnFinish}
              title={t("code.chat.notify.hint")}
              onClick={() => {
                const next = !notifyOnFinish;
                setNotifyOnFinish(next);
                localStorage.setItem(
                  "chimera.notifyOnFinish",
                  next ? "1" : "0",
                );
              }}
            >
              {t("code.chat.notify.label")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void clear()}>
              <Eraser className="h-3.5 w-3.5" /> {t("code.chat.clear")}
            </Button>
          </div>
        ) : null}
      </div>
      {/* Said out loud rather than logged. "Your export is missing four turns" is exactly the kind
          of thing that must not be discovered later, by someone reading the file. */}
      {exportNote ? (
        <p className="border-b border-hairline px-3 py-1 text-xs text-warn">
          {exportNote}
        </p>
      ) : null}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto">
        {/* `role="log"` tells a screen reader this region accumulates; `aria-busy` says the agent is
            still writing. The streaming text is deliberately NOT a live region — announcing it would
            re-read the whole growing answer on every token. */}
        <div
          role="log"
          aria-busy={busy}
          className="mx-auto max-w-3xl space-y-3 p-3"
        >
          {exchanges.length === 0 && replayed ? (
            <div className="flex flex-col items-center justify-center py-20 text-center">
              <BrandMark className="mb-4 h-14 w-14" glow />
              <h2 className="text-base font-semibold">Chimera</h2>
              <p className="mt-1 max-w-sm text-sm text-muted-foreground">
                {t("code.chat.empty")}
              </p>
            </div>
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
                // Markdown with syntax highlighting, which the chat had and this did not — a coding
                // conversation rendering a fenced block as prose is the one formatting failure that
                // matters here.
                <div className="group relative">
                  <div className="md min-w-0 text-sm leading-relaxed text-foreground/90">
                    <Markdown rehypePlugins={[rehypeHighlight]}>
                      {e.answer}
                    </Markdown>
                  </div>
                  {/* Copies the exchange as MARKDOWN, not the rendered text: what the reader wants to
                    paste into an issue is the fenced code that made it worth reading, and the
                    rendered version loses exactly that. */}
                  <button
                    type="button"
                    aria-label={t("code.chat.copyAnswer")}
                    title={t("code.chat.copyAnswer")}
                    className="absolute right-0 top-0 rounded-chip p-1 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100 focus:opacity-100"
                    onClick={() => {
                      void navigator.clipboard
                        ?.writeText(exchangeToMarkdown(e))
                        .catch(() => undefined);
                    }}
                  >
                    <Copy className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : null}
              {e.failed ? (
                <div className="space-y-1">
                  <p className="text-xs text-bad">{t("code.chat.error")}</p>
                  {/* Folded, not hidden: the headline stays one line for the common case where the
                    user only wants to retry, and the raw provider message is one click away for
                    the case where it says `invalid_api_key` and settles the whole question. */}
                  {e.error ? (
                    <details className="text-xs">
                      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                        {t("code.chat.errorDetail")}
                      </summary>
                      <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded-chip bg-surface-2 p-2 font-mono text-muted-foreground">
                        {e.error}
                      </pre>
                    </details>
                  ) : null}
                </div>
              ) : null}
              {e.done?.fused ? (
                // At the answer, which is the only place it lands in time. The composer warns before
                // the click; neither warning is visible at the moment someone reads a confident
                // description of a file that was never opened.
                <p className="flex items-start gap-1.5 text-xs text-warn">
                  <Network className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {t("composer.fusedAnswer")}
                </p>
              ) : null}
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
        </div>
      </div>
      {/* Only while there is something to miss: reading back through a finished answer should not
          nag you to return to the bottom. */}
      {!stuck && busy ? (
        <button
          type="button"
          onClick={scrollToBottom}
          className="absolute bottom-24 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-chip border border-border bg-surface-2 px-3 py-1.5 text-xs shadow-btn"
        >
          <ArrowDown className="h-3.5 w-3.5" /> {t("code.chat.jumpToLatest")}
        </button>
      ) : null}

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
        <AttachmentTray
          items={attached}
          model={model}
          onRemove={(id) => setAttached((p) => p.filter((a) => a.id !== id))}
        />
        {/* Shown, never silent. A message that vanished into an invisible queue is indistinguishable
            from one that was dropped, and the user would retype it — which is two sends, not one. */}
        {queued ? (
          <div className="mb-1.5 flex items-start gap-2 rounded-chip border border-hairline bg-surface-2 px-2 py-1.5">
            <span className="shrink-0 text-xs text-muted-foreground">
              {t("composer.queued")}
            </span>
            <span className="min-w-0 flex-1 truncate text-xs">{queued}</span>
            <button
              type="button"
              className="shrink-0 text-xs text-muted-foreground hover:text-bad"
              // Back into the box rather than deleted: the user wrote it, and a cancel that destroys
              // text is a worse trade than one that hands it back.
              onClick={() => {
                setDraft((d) => (d ? `${queued}\n${d}` : queued));
                setQueued(null);
              }}
            >
              {t("composer.unqueue")}
            </button>
          </div>
        ) : null}
        <textarea
          className={cn(
            "field min-h-[64px] w-full resize-y px-3 py-2 text-sm",
            dragging && "ring-2 ring-accent",
          )}
          placeholder={t("code.chat.placeholder")}
          value={draft}
          onChange={(ev) => setDraft(ev.target.value)}
          // Paste is the point. Taking a screenshot and pressing Ctrl+V is how people show a
          // program what is wrong with it; without this the clipboard image was dropped on the
          // floor and the only route was Save As, then the file dialog. `items` is where a pasted
          // image lives — `clipboardData.files` is empty for it on some platforms — and pasted TEXT
          // must still paste as text, so this only intercepts when a file actually comes out.
          onPaste={(ev) => {
            const files = Array.from(ev.clipboardData?.items ?? [])
              .filter((i) => i.kind === "file")
              .map((i) => i.getAsFile())
              .filter((f): f is File => f !== null);
            if (files.length === 0) return;
            ev.preventDefault();
            void upload(files);
          }}
          // Drag-and-drop needs its own preventDefault on dragOver or the browser navigates away to
          // the dropped file — losing the whole conversation, which is a far worse outcome than not
          // supporting drops at all.
          onDragOver={(ev) => {
            if (!ev.dataTransfer?.types?.includes("Files")) return;
            ev.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(ev) => {
            if (!ev.dataTransfer?.files?.length) return;
            ev.preventDefault();
            setDragging(false);
            void upload(ev.dataTransfer.files);
          }}
          onKeyDown={(ev) => {
            // Enter sends, Shift+Enter breaks the line — the chat's habit, chosen for the merged
            // screen. It is the riskier of the two now that a turn edits files, and what makes it
            // affordable is that the turn is verified and the edits are reversible.
            if (ev.key === "Enter" && !ev.shiftKey) {
              ev.preventDefault();
              send();
            }
          }}
          // NOT disabled while busy. Blocking the box was the whole complaint: a follow-up that
          // occurs to you mid-turn had nowhere to go, so it was either lost or typed elsewhere and
          // pasted back. Send still refuses to start a second turn — it queues instead.
        />
        {/* A paste that failed must say so here. The paperclip reports its own failures next to
            itself; a file that arrived by clipboard has no button to stand beside, and silence
            would leave someone waiting for a screenshot that was never uploaded. */}
        {uploadFailed ? (
          <p className="text-xs text-bad">
            {t("code.attach.failed", { name: uploadFailed })}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <AttachButton onAdded={(a) => setAttached((prev) => [...prev, a])} />
          <DictateButton
            onText={(text) =>
              setDraft((prev) => (prev ? `${prev} ${text}` : text))
            }
          />
          {/* Fusion is a per-turn choice, next to the box you type in — and it turns OFF the
              agent's ability to act, which the tooltip says before the click and `fusedAnswer` says
              at the answer. */}
          <Button
            size="sm"
            variant={fuse ? "primary" : "ghost"}
            aria-pressed={fuse}
            title={t("composer.fuseHint")}
            onClick={() => setFuse((f) => !f)}
          >
            <Network className="h-4 w-4" /> {t("composer.fuse")}
          </Button>
          {/* Only while fusion is armed. Who plays each part is a real decision — three models, six
              calls — and it is noise on every turn that is not fused. */}
          {fuse ? (
            <FusionCast value={cast} onChange={setCast} disabled={busy} />
          ) : null}
          {/* What this turn may COST, next to what it may DO. Both are per-turn choices, and this
              one is the only limit in the app that can end a turn on its own — so it belongs where
              the decision is made rather than in a settings screen visited once. */}
          <SpendCeiling onChange={setMaxUsd} disabled={busy} />
          {busy ? (
            <Button size="sm" variant="outline" onClick={abandon}>
              <Square className="h-4 w-4" /> {t("code.chat.stop")}
            </Button>
          ) : (
            <Button
              size="sm"
              disabled={!draft.trim() || busyElsewhere}
              onClick={() => send()}
            >
              <Send className="h-4 w-4" /> {t("code.chat.send")}
            </Button>
          )}
          {/* Three warnings, at three moments, because each catches a different person: the tooltip
              before the click, this while the toggle is armed and the message is being typed, and
              the mark on the answer for whoever was not reading either. */}
          <span
            className={cn(
              "ml-auto text-xs",
              fuse ? "text-warn" : "text-muted-foreground",
            )}
          >
            {fuse ? t("composer.fuseOn") : t("code.chat.hint")}
          </span>
        </div>
      </div>
    </div>
  );
}
