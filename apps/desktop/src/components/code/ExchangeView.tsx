import { memo, useMemo, useRef, type MutableRefObject } from "react";
import rehypeHighlight from "rehype-highlight";
import { Copy, Loader2, Network, RotateCcw, Wrench } from "lucide-react";
import { BatchRecord } from "@/components/code/BatchRecord";
import { BrowserView } from "@/components/code/BrowserView";
import { CardChrome, cardId, type CardModes } from "@/components/code/CardChrome";
import { ChartCard } from "@/components/code/ChartView";
import { DesignFrameCard, DesignSuggestionCard } from "@/components/code/DesignCards";
import { EditDiff } from "@/components/code/EditDiff";
import { Elapsed } from "@/components/code/Elapsed";
import { NoticeList } from "@/components/code/NoticeList";
import { PolicyBlocked } from "@/components/code/PolicyBlocked";
import { StepsSummary } from "@/components/code/StepsSummary";
import { TaskSuggestionCards, type TaskStartOptions } from "@/components/code/TaskSuggestionCards";
import type { TaskCardState } from "@/lib/taskSuggestions";
import { TodoPanel } from "@/components/code/TodoPanel";
import { ToolRow } from "@/components/code/ToolRow";
import { TurnSuggestions } from "@/components/code/TurnSuggestions";
import {
  ContextMeter,
  TurnOutputs,
  TurnReceipt,
  Verdict,
  type Exchange,
  type PolicyRetry,
} from "@/components/code/Conversation";
import { SafeMarkdown } from "@/components/markdown/SafeMarkdown";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import type { Suggestion } from "@/lib/suggestions";
import { exchangeToMarkdown } from "@/lib/transcript";
import { viewShowsToolDetail, type TranscriptView } from "@/lib/transcriptView";
import { cn } from "@/lib/utils";

/**
 * What one exchange can ask of the screen that owns it.
 *
 * A memoised component is only as good as the identity of its props. `Conversation` declares these
 * as plain functions in its body, which are new on every render, so passing them straight down would
 * make `memo` compare unequal on every token and do nothing — silently, with no error. They are
 * handed over through `useStableActions`, which keeps one object for the life of the screen and
 * forwards each call to the latest closure.
 */
export interface ExchangeActions {
  openFile: (path: string) => void;
  openDesign: (id: string | null, brief?: string) => void;
  send: (force?: boolean, override?: string, spoken?: boolean, auto?: boolean, retry?: PolicyRetry) => boolean;
  undo: (index: number, token: string) => Promise<void>;
  handOff: (text: string) => void;
  startTaskCard: (card: TaskCardState, options: TaskStartOptions) => void;
  removeTaskCard: (id: string) => void;
  pickSuggestion: (item: Suggestion) => void;
}

/**
 * One stable object whose methods always call the LATEST version of `latest`'s.
 *
 * The keys must not change between renders. The object itself never does, which is the point.
 */
export function useStableActions(latest: ExchangeActions): ExchangeActions {
  const ref = useRef(latest);
  ref.current = latest;
  return useMemo(() => {
    const forward = <K extends keyof ExchangeActions>(key: K) =>
      ((...args: unknown[]) =>
        (ref.current[key] as (...a: unknown[]) => unknown)(...args)) as ExchangeActions[K];
    return {
      openFile: forward("openFile"),
      openDesign: forward("openDesign"),
      send: forward("send"),
      undo: forward("undo"),
      handOff: forward("handOff"),
      startTaskCard: forward("startTaskCard"),
      removeTaskCard: forward("removeTaskCard"),
      pickSuggestion: forward("pickSuggestion"),
    };
  }, []);
}

export interface ExchangeViewProps {
  e: Exchange;
  i: number;
  /** The last exchange: the only one that moves while a turn streams, and the one the retry,
   *  the suggestions and the "working" line belong to. */
  isLast: boolean;
  busy: boolean;
  busyElsewhere: boolean;
  fuse: boolean;
  runLive: boolean;
  workspace: string;
  sessionId: string | null;
  transcriptView: TranscriptView;
  cards: CardModes;
  /** Only the last exchange is given the suggestions; the others get `undefined`, so a new offer
   *  does not re-render the whole history. */
  suggestions: Suggestion[] | undefined;
  actions: ExchangeActions;
  hasOpenFile: boolean;
  hasOpenDesign: boolean;
  taskCardRef: MutableRefObject<string | null>;
}

/** One array for the life of the module — a fresh `[rehypeHighlight]` per render is a changed prop. */
const REHYPE_PLUGINS = [rehypeHighlight];

/**
 * The answer's markdown, apart from the rest of the exchange: a tool row or a todo changing in the
 * active exchange re-renders the card around it, not the parse of a long answer.
 */
const AnswerMarkdown = memo(function AnswerMarkdown({ text, workspace }: { text: string; workspace: string }) {
  return (
    // The conversation's workspace: a relative path in the answer is a file there.
    <SafeMarkdown rehypePlugins={REHYPE_PLUGINS} workspace={workspace}>
      {text}
    </SafeMarkdown>
  );
});


/**
 * One turn of the conversation: what was asked, what the agent did, what it answered.
 *
 * Extracted from the body of `Conversation`'s `exchanges.map` and memoised (P1-09). Before, every
 * streamed token re-rendered every exchange, including the markdown of each finished answer. Now an
 * exchange whose props did not change is skipped: `patchLast` keeps the identity of every exchange
 * but the last, and the callbacks arrive through `ExchangeActions`.
 */
export const ExchangeView = memo(function ExchangeView({
  e,
  i,
  isLast,
  busy,
  busyElsewhere,
  fuse,
  runLive,
  workspace,
  sessionId,
  transcriptView,
  cards,
  suggestions,
  actions,
  hasOpenFile,
  hasOpenDesign,
  taskCardRef,
}: ExchangeViewProps) {
  const t = useT();
  const onOpenFile = hasOpenFile ? actions.openFile : undefined;
  const onOpenDesign = hasOpenDesign ? actions.openDesign : undefined;
  const { send, undo, startTaskCard, removeTaskCard, pickSuggestion } = actions;
  const onHandOff = actions.handOff;
  return (
        <div className="space-y-2">
          <div className="rounded-chip bg-surface-2 px-2.5 py-1.5 text-sm text-foreground/90">
            {/* A guest's turn says whose it is. The owner's own turns say nothing, as they
                always have — every turn before sharing existed was theirs. */}
            {e.author ? (
              <span
                className="mr-1.5 rounded-chip bg-accent/15 px-1.5 text-xs font-medium text-accent-ink"
                data-testid="exchange-author"
              >
                {e.author}
              </span>
            ) : null}
            {e.you}
            {e.selection ? (
              <span className="mt-1 block text-xs text-muted-foreground" data-testid="exchange-selection">
                {t("code.selection.from", {
                  file: e.selection.path,
                  lines:
                    e.selection.start_line === e.selection.end_line
                      ? String(e.selection.start_line)
                      : `${e.selection.start_line}–${e.selection.end_line}`,
                })}
              </span>
            ) : null}
          </div>
          {e.startedAt !== undefined || e.finalSeconds !== undefined || e.done?.elapsed_seconds !== undefined ? (
            <div className="flex items-center gap-2 text-xs" data-testid="turn-clock">
              <Elapsed
                startedAt={e.startedAt}
                finalSeconds={e.finalSeconds ?? e.done?.elapsed_seconds}
                testId="turn-elapsed"
              />
              {e.context && !e.done && !e.failed && !e.abandoned ? (
                <ContextMeter used={e.context.used} window={e.context.window} />
              ) : null}
            </div>
          ) : null}
          {e.tools.length > 0 || e.running ? (
            <CardChrome id={cardId(i, "tools")} kind="tools" cards={cards}
              summary={t("layout.card.count", { name: t("layout.card.name.tools"), n: e.tools.length })}>
            {e.done ? (
              <StepsSummary tools={e.tools} edits={e.edits} detailed={viewShowsToolDetail(transcriptView)} onOpenFile={onOpenFile} />
            ) : (
              <div className="space-y-1 rounded-chip border border-border p-2">
                <div className="flex items-center gap-1.5 text-xs uppercase tracking-wider text-muted-foreground">
                  <Wrench className="h-3 w-3" /> {t("code.chat.tools")}
                </div>
                {e.tools.map((tool, j) => (
                  <ToolRow key={j} tool={tool} onOpenFile={onOpenFile} detailed={viewShowsToolDetail(transcriptView)} />
                ))}
                {e.running ? (
                  <div className="flex items-baseline gap-2 font-mono text-xs" data-testid="running-step">
                    <Loader2 className="h-3 w-3 shrink-0 animate-spin self-center text-muted-foreground" />
                    <span className="shrink-0 text-foreground/80">{e.running.name}</span>
                    <Elapsed startedAt={e.running.startedAt} testId="step-elapsed" />
                    <span className="truncate text-muted-foreground">
                      {String(Object.values(e.running.arguments)[0] ?? "")}
                    </span>
                  </div>
                ) : null}
              </div>
            )}
            </CardChrome>
          ) : null}
          {e.todos.length > 0 ? (
            <CardChrome id={cardId(i, "todo")} kind="todo" cards={cards}>
              <TodoPanel items={e.todos} />
            </CardChrome>
          ) : null}
          {e.notices?.length ? (
            <CardChrome id={cardId(i, "notices")} kind="notices" cards={cards}
              summary={t("layout.card.count", { name: t("layout.card.name.notices"), n: e.notices.length })}>
              <NoticeList items={e.notices} />
            </CardChrome>
          ) : null}
          {e.browser ? (
            <CardChrome id={cardId(i, "browser")} kind="browser" cards={cards}>
              <BrowserView frame={e.browser} />
            </CardChrome>
          ) : null}
          {e.designSuggestion ? (
            <CardChrome id={cardId(i, "design")} kind="design" cards={cards} summary={t("design.card.suggestTitle")}>
              <DesignSuggestionCard suggestion={e.designSuggestion} onOpen={onOpenDesign ? () => onOpenDesign(null, e.you) : undefined} />
            </CardChrome>
          ) : null}
          {e.designs?.map((frame, j) => (
            <CardChrome key={frame.document_id} id={cardId(i, "design", j + 1)} kind="design" cards={cards} summary={frame.title}>
              <DesignFrameCard frame={frame} onOpen={onOpenDesign ? () => onOpenDesign(frame.document_id) : undefined} />
            </CardChrome>
          ))}
          {e.charts?.map((chart, j) => (
            <CardChrome key={j} id={cardId(i, "chart", j)} kind="chart" cards={cards}
              summary={chart.title ?? chart.path}>
              <ChartCard frame={chart} onOpenFile={onOpenFile} />
            </CardChrome>
          ))}
          {e.edits.map((edit, j) => {
            return (
              <CardChrome key={j} id={cardId(i, "diff", j)} kind="diff" cards={cards} summary={edit.path}>
                <EditDiff path={edit.path} patch={edit.patch} detailed={viewShowsToolDetail(transcriptView)} onOpenFile={onOpenFile} commentScope={sessionId ? { sessionId, origin: `edit:${i}:${j}` } : undefined} />
              </CardChrome>
            );
          })}
          {/* Working, with nothing yet to show for it.
              Under fusion the backend emits no token frames at all — deliberately, since the
              panel has no single stream to forward and a fabricated cursor would be a lie told
              in motion. The cost of that honesty was a screen where a fifteen-minute turn and
              a dead one were pixel-identical: the question, and below it nothing. This says
              which of the two it is, and under fusion says why the text is not arriving. */}
          {busy && isLast && !e.answer && !e.done && !e.failed ? (
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              {fuse && !e.policyRetry ? t("code.chat.workingFused") : t("code.chat.working")}
            </p>
          ) : null}
          {e.answer ? (
            // Markdown with syntax highlighting, which the chat had and this did not — a coding
            // conversation rendering a fenced block as prose is the one formatting failure that
            // matters here.
            <div className="group relative">
              <div className="md min-w-0 text-sm leading-relaxed text-foreground/90">
                {/* The conversation's workspace: a relative path in the answer is a file there. */}
                <AnswerMarkdown text={e.answer} workspace={workspace} />
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
          {e.guidance?.map((text, index) => (
            <p key={`${index}:${text}`} className="text-xs text-muted-foreground" data-testid="delivered-guidance">
              {t("code.chat.guidanceDelivered")}: {text}
            </p>
          ))}
          {e.abandoned ? (
            // Muted, not `bad`: stopping is a decision, not a failure, and the tone is what
            // tells those two apart before any word is read.
            <p className="text-xs text-muted-foreground">{t("code.chat.abandoned")}</p>
          ) : null}
          {e.failed ? (
            // Minimises to its first line and never closes: a turn that failed must not look like a
            // turn that finished.
            <CardChrome id={cardId(i, "error")} kind="error" cards={cards} summary={t("code.chat.error")}>
            <div className="space-y-1">
              {/* A refusal on content policy is said as one, with what refused it, and its way
                  forward is the owner's pick of another model — not "Try again" on the model
                  that just refused (study 29 P5.7). */}
              {e.blocked ? (
                <PolicyBlocked
                  block={e.blocked}
                  canRetry={isLast && !busy && !busyElsewhere}
                  filesMissing={e.attachments === undefined}
                  onRetry={(picked) => {
                    const blocked = e.blocked;
                    // Unknown files are not sent as no files: see `turn_started` in applyLive.
                    if (!blocked || e.attachments === undefined) return;
                    send(true, e.you, false, false, {
                      model: picked,
                      of: blocked.model
                        ? { blocked_model: blocked.model, request_id: blocked.request_id }
                        : undefined,
                      attachments: e.attachments,
                    });
                  }}
                  t={t}
                />
              ) : (
                <p className="text-xs text-bad-foreground">{t("code.chat.error")}</p>
              )}
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
              {/* The button the comment above has been describing since it was written — "the
                  common case where the user only wants to retry" had no way to retry. A failed
                  turn is usually a provider hiccup, a rate limit or a dropped connection, and
                  the answer to all three is the same request again. Without this the text has
                  to be retyped, and on a long prompt it is simply lost.
                  Only on the LAST exchange: re-sending a message from the middle of a
                  conversation would append it at the end, in a context that has moved on. */}
              {isLast && !busy && !busyElsewhere && !e.blocked ? (
                <Button size="sm" variant="ghost" onClick={() => send(true, e.you)}>
                  <RotateCcw className="h-3.5 w-3.5" /> {t("common.retry")}
                </Button>
              ) : null}
            </div>
            </CardChrome>
          ) : null}
          {e.done?.fused ? (
            // At the answer, which is the only place it lands in time. The composer warns before
            // the click; neither warning is visible at the moment someone reads a confident
            // description of a file that was never opened.
            <p className="flex items-start gap-1.5 text-xs text-warn-foreground">
              <Network className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {t("composer.fusedAnswer")}
            </p>
          ) : null}
          {e.verified ? (
            <CardChrome id={cardId(i, "verdict")} kind="verdict" cards={cards}>
            <Verdict
              v={e.verified}
              undone={e.undone}
              keptFiles={e.keptFiles}
              onUndo={() => void undo(i, e.verified?.revert_token ?? "")}
              original={e.you}
              onFix={onHandOff}
              fixBlocked={runLive}
              t={t}
            />
            </CardChrome>
          ) : null}
          {e.done ? (
            <CardChrome id={cardId(i, "receipt")} kind="receipt" cards={cards}>
              <TurnReceipt done={e.done} t={t} />
              <TurnOutputs tools={e.tools} edits={e.edits} onOpenFile={onOpenFile} t={t} />
            </CardChrome>
          ) : null}
          {/* A finished batch this exchange started: after it, in the agents' own words. */}
          {e.batches?.map((note) => <BatchRecord key={note.at} note={note} />)}
          {e.taskCards?.length ? (
            <TaskSuggestionCards
              cards={e.taskCards}
              busy={busy || busyElsewhere}
              onStart={startTaskCard}
              onStartAll={(batch) =>
                batch.forEach((c) => startTaskCard(c, { model: "", newWorktree: false }))
              }
              onHere={(c) => {
                // Behind a running turn the prompt would queue, so the card waits for the owner to send it.
                if (busy || busyElsewhere) return;
                // Only the id goes to the server, which sends the prompt it kept and carries the
                // card's taint into the turn. Forced, so the prompt is not split into a batch.
                taskCardRef.current = c.id;
                send(true, c.prompt);
                const sent = taskCardRef.current === null;
                taskCardRef.current = null;
                if (sent) removeTaskCard(c.id);
              }}
              onDismiss={(c) => removeTaskCard(c.id)}
            />
          ) : null}
          {isLast ? <TurnSuggestions items={suggestions ?? []} onPick={pickSuggestion} /> : null}
          {/* What was closed in this turn, and the way back to it, where it was. */}
          {cards.closedInTurn(i) > 0 ? (
            <button
              type="button"
              onClick={() => cards.showTurn(i)}
              className={cn(
                "rounded-chip border border-dashed border-hairline px-2.5 py-0.5 text-xs text-muted-foreground",
                "transition-colors duration-1 ease-out hover:border-accent hover:text-foreground",
              )}
            >
              {t("layout.card.hiddenInTurn", { n: cards.closedInTurn(i) })}
            </button>
          ) : null}
        </div>
  );
});
