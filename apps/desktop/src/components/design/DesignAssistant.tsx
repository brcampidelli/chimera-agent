import { useId, useState, type ReactNode } from "react";
import { Loader2, Sparkles } from "lucide-react";

import { DesignPreview } from "@/components/design/DesignPreview";
import { FindingList } from "@/components/design/FindingList";
import type { DesignSession } from "@/components/design/useDesignSession";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import {
  ApiError,
  auditDesign,
  proposeDesign,
  refineDesign,
  type DesignAudit,
  type DesignProposal,
} from "@/lib/api";
import { useNum, useT } from "@/lib/i18n";

/** The server's cap on a brief or a request (`MAX_BRIEF` in chimera/design/propose.py). */
const MAX_BRIEF = 8_000;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 border-b border-hairline p-3">
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function Bullets({ title, items }: { title: string; items: readonly string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-muted-foreground">{title}</span>
      <ul className="list-disc pl-4 text-sm text-foreground">
        {items.map((item, i) => (
          <li key={i} className="break-words">
            {item}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Why a model call failed, in the reader's language; the server's own sentence when it has one. */
function failure(t: ReturnType<typeof useT>, error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return t("design.assistant.noModel");
  return t("design.assistant.failed");
}

/**
 * Asking the model, beside the canvas. Every answer is a PROPOSAL: the server validated it and ran it
 * dry, and the panel shows the summary, the assumptions, the checks and a picture of the result. The
 * canvas changes only when the owner presses Apply, and the change is then one step to undo.
 */
export function DesignAssistant({
  designId,
  session,
  initialBrief = "",
  selection,
  htmlSelection,
  onJump,
  onNavigate,
}: {
  designId: string;
  session: DesignSession;
  initialBrief?: string;
  selection: readonly string[];
  htmlSelection?: { artboardId: string; index: number; tag: string; inlineStyle: string; text: string | null } | null;
  onJump: (ids: string[]) => void;
  onNavigate: (artboardId: string) => void;
}) {
  const t = useT();
  const num = useNum();
  const briefId = useId();
  const requestId = useId();
  const [brief, setBrief] = useState(initialBrief);
  const [request, setRequest] = useState("");
  const [busy, setBusy] = useState<"propose" | "refine" | "audit" | "apply" | null>(null);
  const [proposal, setProposal] = useState<DesignProposal | null>(null);
  const [audit, setAudit] = useState<DesignAudit | null>(null);
  const [error, setError] = useState("");

  async function ask(kind: "propose" | "refine" | "audit"): Promise<void> {
    setBusy(kind);
    setError("");
    try {
      // The model reads the SAVED design; anything still queued goes first.
      await session.flush();
      if (kind === "audit") {
        setAudit(await auditDesign(designId, brief.trim()));
      } else {
        setProposal(
          kind === "propose"
            ? await proposeDesign(designId, brief.trim())
            : htmlSelection
              ? await refineDesign(designId, request.trim(), selection, htmlSelection)
              : await refineDesign(designId, request.trim(), selection),
        );
      }
    } catch (e) {
      setError(failure(t, e));
    } finally {
      setBusy(null);
    }
  }

  async function apply(): Promise<void> {
    if (!proposal) return;
    setBusy("apply");
    const ok = await session.applyProposal(proposal);
    setBusy(null);
    if (ok) setProposal(null);
    else setError(t("design.assistant.stale"));
  }

  const spin = (kind: typeof busy) => (busy === kind ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null);

  return (
    <div className="flex flex-col" aria-busy={busy !== null}>
      <Section title={t("design.assistant.proposeTitle")}>
        <label htmlFor={briefId} className="text-xs text-muted-foreground">
          {t("design.assistant.brief")}
        </label>
        <textarea
          id={briefId}
          value={brief}
          rows={4}
          maxLength={MAX_BRIEF}
          onChange={(e) => setBrief(e.target.value)}
          placeholder={t("design.assistant.briefPlaceholder")}
          className="field resize-y px-2 py-1 text-sm"
        />
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" disabled={busy !== null || brief.trim() === ""} onClick={() => void ask("propose")}>
            {spin("propose") ?? <Sparkles className="h-4 w-4" aria-hidden="true" />}
            {t("design.assistant.propose")}
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={busy !== null} onClick={() => void ask("audit")}>
            {spin("audit")}
            {t("design.assistant.audit")}
          </Button>
        </div>
      </Section>
      <Section title={t("design.assistant.refineTitle")}>
        <label htmlFor={requestId} className="text-xs text-muted-foreground">
          {t("design.assistant.request", { n: num(selection.length) })}
        </label>
        <textarea
          id={requestId}
          value={request}
          rows={2}
          maxLength={MAX_BRIEF}
          onChange={(e) => setRequest(e.target.value)}
          className="field resize-y px-2 py-1 text-sm"
        />
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={busy !== null || request.trim() === "" || selection.length === 0}
          onClick={() => void ask("refine")}
        >
          {spin("refine")}
          {t("design.assistant.refine")}
        </Button>
      </Section>
      {error ? <p role="alert" className="px-3 pt-2 text-sm text-bad-foreground">{error}</p> : null}
      {proposal ? (
        <Section title={t("design.assistant.proposalTitle")}>
          {proposal.ok ? null : (
            <p className="text-sm text-bad-foreground">{t("design.assistant.unusable", { reason: proposal.error ?? "" })}</p>
          )}
          {proposal.summary ? <p className="break-words text-sm text-foreground">{proposal.summary}</p> : null}
          <Bullets title={t("design.assistant.assumptions")} items={proposal.assumptions} />
          <Bullets title={t("design.assistant.checks")} items={proposal.checks} />
          <Bullets title={t("design.assistant.questions")} items={proposal.questions} />
          {proposal.preview ? <DesignPreview doc={proposal.preview} onNavigate={onNavigate} /> : null}
          <p className="text-xs text-muted-foreground">
            {t("design.assistant.operations", { n: num(proposal.operations.length) })}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              size="sm"
              disabled={busy !== null || !proposal.ok || proposal.operations.length === 0}
              onClick={() => void apply()}
            >
              {spin("apply")}
              {t("design.assistant.apply")}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setProposal(null)}>
              {t("design.assistant.discard")}
            </Button>
          </div>
        </Section>
      ) : null}
      {audit ? (
        <Section title={t("design.assistant.auditTitle")}>
          {audit.ok ? null : <p className="text-sm text-bad-foreground">{t("design.assistant.failed")}</p>}
          <ul className="flex flex-col gap-2">
            {audit.issues.map((issue, i) => (
              <li key={i} className="flex flex-col gap-1 text-sm">
                <span className="flex items-center gap-2">
                  <Badge tone={issue.severity === "high" ? "bad" : issue.severity === "medium" ? "warn" : "muted"}>
                    {t(issue.severity === "high" ? "design.severity.high" : issue.severity === "medium" ? "design.severity.medium" : "design.severity.low")}
                  </Badge>
                  {issue.node_id ? (
                    <button type="button" className="text-xs text-accent-ink underline decoration-dotted" onClick={() => onJump([issue.node_id as string])}>
                      {t("design.check.jump")}
                    </button>
                  ) : null}
                </span>
                <span className="break-words text-foreground">{issue.evidence}</span>
                {issue.fix ? <span className="break-words text-muted-foreground">{issue.fix}</span> : null}
              </li>
            ))}
          </ul>
          <Bullets title={t("design.assistant.passed")} items={audit.passed_checks} />
          <Bullets title={t("design.assistant.decide")} items={audit.needs_user_decision} />
          <FindingList findings={audit.findings} onJump={onJump} />
        </Section>
      ) : null}
    </div>
  );
}
