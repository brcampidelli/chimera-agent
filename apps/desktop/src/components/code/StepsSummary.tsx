import { useState } from "react";

import { ToolRow } from "@/components/code/ToolRow";
import { formatElapsed } from "@/components/code/Elapsed";
import { type Outcome, type OutcomeKind, summarizeSteps } from "@/components/code/stepSummary";
import type { CodeToolEvent } from "@/lib/api";
import { type TFunc, useT } from "@/lib/i18n";

/** A finished turn's tool calls as one receipt: how many, how many worked, what it did, and the
 *  full list one click away. The detailed view keeps every call expanded, as it always did. */
export function StepsSummary({
  tools,
  edits,
  detailed,
  onOpenFile,
}: {
  tools: readonly CodeToolEvent[];
  edits: readonly { path: string }[];
  detailed: boolean;
  onOpenFile?: (path: string) => void;
}) {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const [failuresOnly, setFailuresOnly] = useState(false);
  const summary = summarizeSteps(tools, edits);

  if (detailed) {
    return (
      <div className="space-y-1 rounded-chip border border-border p-2">
        {tools.map((tool, index) => (
          <ToolRow key={index} tool={tool} onOpenFile={onOpenFile} detailed />
        ))}
      </div>
    );
  }

  const shown = tools.filter((tool) => !failuresOnly || !tool.ok);
  // The chip is a switch: the first press opens the failed calls, the second folds them away again.
  // `aria-expanded` is the same question as what is on screen, so it cannot drift from it.
  const failuresShown = expanded && failuresOnly;
  const toggleFailures = () => {
    if (failuresShown) {
      setExpanded(false);
      setFailuresOnly(false);
    } else {
      setFailuresOnly(true);
      setExpanded(true);
    }
  };

  return (
    <section className="rounded-chip border border-border px-2.5 py-2 text-xs" data-testid="steps-summary">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-foreground">
          {t("code.chat.stepsIn", { n: summary.count, time: formatElapsed(summary.elapsed, t) })}
        </span>
        <span className="rounded-chip bg-ok/10 px-1.5 py-0.5 text-ok-foreground" data-testid="steps-ok">
          {t("code.chat.steps.ok", { n: summary.succeeded })}
        </span>
        {summary.failed > 0 ? (
          <button
            type="button"
            className="rounded-chip bg-warn/10 px-1.5 py-0.5 text-warn-foreground"
            aria-expanded={failuresShown}
            onClick={toggleFailures}
            data-testid="steps-failed"
          >
            {t("code.chat.steps.failed", { n: summary.failed })}
            {summary.failedTool ? ` · ${summary.failedTool}` : ""}
          </button>
        ) : null}
        <button
          type="button"
          className="ml-auto text-accent-ink underline decoration-dotted"
          aria-expanded={expanded}
          onClick={() => {
            setExpanded((value) => !value);
            setFailuresOnly(false);
          }}
        >
          {expanded ? t("code.chat.steps.hide") : t("code.chat.steps.show")}
        </button>
      </div>
      <p className="mt-2 text-muted-foreground">{t("code.chat.steps.what")}</p>
      <ul className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-2">
        {summary.outcomes.map((outcome, index) => (
          <li key={`${outcome.kind}-${index}`} className="min-w-0">
            <OutcomeItem outcome={outcome} t={t} onOpenFile={onOpenFile} />
          </li>
        ))}
      </ul>
      {expanded ? (
        <div className="mt-2 space-y-1 border-t border-hairline pt-2">
          {shown.map((tool, index) => (
            <ToolRow key={index} tool={tool} onOpenFile={onOpenFile} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

/** Literal keys, so every label is reachable by the dictionary's own check. */
const KIND_LABEL: Record<OutcomeKind, (t: TFunc) => string> = {
  file: (t) => t("code.chat.steps.kind.file"),
  server: (t) => t("code.chat.steps.kind.server"),
  media: (t) => t("code.chat.steps.kind.media"),
  verify: (t) => t("code.chat.steps.kind.verify"),
};

function OutcomeItem({
  outcome,
  t,
  onOpenFile,
}: {
  outcome: Outcome;
  t: TFunc;
  onOpenFile?: (path: string) => void;
}) {
  const kind = KIND_LABEL[outcome.kind](t);
  if (outcome.kind === "verify" && outcome.label === null) {
    return <span className="text-muted-foreground">{kind}: {t("code.chat.verify.none")}</span>;
  }
  if (outcome.kind === "file" && outcome.label) {
    return (
      <span className="flex min-w-0 items-baseline gap-1.5">
        <span className="shrink-0 text-muted-foreground">{kind}</span>
        <button
          type="button"
          onClick={() => onOpenFile?.(outcome.label as string)}
          className="min-w-0 truncate text-left font-mono text-accent-ink underline decoration-dotted"
          title={outcome.label}
        >
          {outcome.label}
        </button>
      </span>
    );
  }
  return (
    <span className="block truncate font-mono text-muted-foreground" title={outcome.label ?? undefined}>
      {kind} · {outcome.label}
    </span>
  );
}
