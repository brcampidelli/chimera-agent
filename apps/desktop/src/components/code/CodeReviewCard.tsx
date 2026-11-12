import { useState } from "react";

import { DiffView } from "@/components/code/DiffView";
import { buildFixMessage, type FixItem } from "@/components/code/codeReview";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useT, type TFunc } from "@/lib/i18n";
import type { CodeReview, CodeReviewFinding } from "@/lib/types";

/**
 * The card `/code-review` leaves in the conversation: findings grouped by file, each with its severity,
 * its place, and what it would cost. Nothing on it changes the code; "Apply fixes" only sends the
 * conversation a request, and the person picks which findings the request names.
 *
 * A review that did not finish says so in its heading and its notes: a card with no findings and
 * `incomplete` status is not a clean change, so the empty list is never shown without its status.
 */

function severityTone(severity: string): "bad" | "warn" | "muted" {
  if (severity === "high") return "bad";
  if (severity === "medium") return "warn";
  return "muted";
}

function severityLabel(t: TFunc, severity: string): string {
  if (severity === "high") return t("code.review.severity.high");
  if (severity === "medium") return t("code.review.severity.medium");
  return t("code.review.severity.low");
}

function statusLine(t: TFunc, review: CodeReview): string {
  switch (review.status) {
    case "findings":
      return t("code.review.count", { n: review.shown });
    case "no_findings":
      return t("code.review.none");
    case "incomplete":
      return t("code.review.incomplete");
    case "empty":
      return t("code.review.empty");
    case "no_diff":
      return t("code.review.noDiff");
    default:
      return t("code.review.failed");
  }
}

export function CodeReviewCard({
  sessionId,
  review,
  busy,
  onApply,
}: {
  sessionId: string;
  review: CodeReview;
  busy: boolean;
  onApply: (message: string) => void;
}) {
  const t = useT();
  // Verified findings start picked. An unverified one starts unpicked: it is shown, but "Apply fixes"
  // sends it only when the person ticks it.
  const shownIds = review.files.flatMap((f) => f.findings.filter((x) => !x.unverified).map((x) => x.id));
  const [picked, setPicked] = useState<Set<string>>(() => new Set(shownIds));
  // The diff a "View in diff" opened: its file, and the line of the finding it was opened for.
  const [opened, setOpened] = useState<{ file: string; line: number } | null>(null);
  const [confirming, setConfirming] = useState(false);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const chosen: FixItem[] = review.files.flatMap((group) =>
    group.findings
      .filter((f) => picked.has(f.id))
      .map((f: CodeReviewFinding) => ({
        severity: severityLabel(t, f.severity),
        file: f.file,
        line: f.line,
        title: f.title,
        consequence: f.consequence,
      })),
  );

  const apply = () => {
    if (chosen.length === 0 || busy) return;
    if (review.tainted && !confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    onApply(buildFixMessage(t("code.review.fixIntro"), chosen));
  };

  return (
    <section aria-label={t("code.review.title")} className="surface space-y-2 p-3 text-sm">
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">{t("code.review.title")}</h3>
        <span className="text-muted-foreground">{statusLine(t, review)}</span>
        {review.model ? (
          <span className="text-xs text-muted-foreground">
            {t("code.review.reviewedBy", { model: review.model })}
          </span>
        ) : null}
        {review.tainted ? <Badge tone="warn">{t("code.review.tainted")}</Badge> : null}
      </header>

      {review.partial ? <p className="text-xs text-bad-foreground">{t("code.review.partial")}</p> : null}
      {review.notes.length > 0 ? (
        <ul className="space-y-0.5 text-xs text-muted-foreground">
          {review.notes.map((note, i) => (
            <li key={i}>{note}</li>
          ))}
        </ul>
      ) : null}

      {review.files.map((group) => (
        <div key={group.file} className="space-y-1">
          <div className="font-mono text-xs text-muted-foreground">{group.file}</div>
          <ul className="space-y-1">
            {group.findings.map((f) => (
              <li key={f.id} className="flex flex-col gap-0.5 rounded-chip px-2 py-1">
                <span className="flex min-w-0 flex-wrap items-center gap-2">
                  <input
                    type="checkbox"
                    checked={picked.has(f.id)}
                    onChange={() => toggle(f.id)}
                    aria-label={t("code.review.select", { id: f.id })}
                  />
                  <Badge tone={severityTone(f.severity)}>{severityLabel(t, f.severity)}</Badge>
                  {f.unverified ? <Badge tone="muted">{t("code.review.unverified")}</Badge> : null}
                  <span className="font-mono text-xs">
                    {f.file}:{f.line}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{f.title}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setOpened(
                        opened?.file === group.file && opened.line === f.line
                          ? null
                          : { file: group.file, line: f.line },
                      )
                    }
                  >
                    {t("code.review.viewDiff")}
                  </Button>
                </span>
                {f.consequence ? (
                  <span className="pl-6 text-xs text-muted-foreground">{f.consequence}</span>
                ) : null}
              </li>
            ))}
          </ul>
          {opened?.file === group.file ? (
            <DiffView
              patch={group.patch}
              commentable={{ sessionId, file: group.file, origin: "review" }}
              focusLine={opened.line}
            />
          ) : null}
        </div>
      ))}

      {review.omitted > 0 ? (
        <p className="text-xs text-muted-foreground">{t("code.review.omitted", { n: review.omitted })}</p>
      ) : null}
      {review.dropped > 0 ? (
        <p className="text-xs text-muted-foreground">{t("code.review.dropped", { n: review.dropped })}</p>
      ) : null}

      {review.files.length > 0 ? (
        <footer className="flex flex-wrap items-center gap-2">
          {confirming ? <span className="text-xs">{t("code.review.applyConfirm")}</span> : null}
          <Button size="sm" disabled={busy || chosen.length === 0} onClick={apply}>
            {t("code.review.apply")}
          </Button>
        </footer>
      ) : null}
    </section>
  );
}
