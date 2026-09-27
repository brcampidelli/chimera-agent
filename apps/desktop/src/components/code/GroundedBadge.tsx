import type { ReactElement } from "react";

import { Badge } from "@/components/ui/panel";
import type { GroundedCheck } from "@/lib/api";
import type { TFunc } from "@/lib/i18n";

/** Why a turn with documents was not checked — literal keys, one per reason word the server sends,
 *  so the i18n reachability test can see each of them. */
const NOT_APPLIED: Record<string, string> = {
  tool_calls: "code.chat.grounded.notApplied.toolCalls",
  not_final: "code.chat.grounded.notApplied.notFinal",
  sources_too_long: "code.chat.grounded.notApplied.tooLong",
};

/** Who checked the answer, how sure it was, and what the check cost — the long form behind the chip.
 *  Technical on purpose (slugs and numbers read the same in every language). */
function detail(g: GroundedCheck): string {
  const parts: string[] = [];
  const v = g.verifier;
  if (v?.model) parts.push(`${v.backend ?? ""}:${v.resolved_model || v.model}`);
  if (v?.fell_back_from?.length) {
    parts.push(`fallback from ${v.fell_back_from.map((s) => `${s.model} (${s.reason})`).join(", ")}`);
  }
  if (g.label) parts.push(`${g.label}${g.p != null ? ` p=${g.p.toFixed(2)}` : ""}`);
  if (g.escalated && g.escalated_model) parts.push(`→ ${g.escalated_model}`);
  if (g.halt) parts.push(g.halt);
  if (g.usd_extra) parts.push(`+$${g.usd_extra.toFixed(4)}`);
  return parts.join(" · ");
}

/**
 * The badge on an answer checked against its attached documents (study 26).
 *
 * Four outcomes that must never render alike: verified, "the sources don't cover this" (a decline
 * shipped on purpose), a lexical check only (no System One verifier could run — it does not read
 * grounding at all), and the verifier unavailable (the answer is unchecked, and says so rather than
 * passing for verified). An answer the verifier withheld stays one click away: withholding is a
 * judgement about the answer, not a reason to lose it.
 */
export function GroundedBadge({ grounded, t }: { grounded?: GroundedCheck | null; t: TFunc }) {
  if (!grounded) return null;
  if (grounded.outcome === "not_applied") {
    const key = NOT_APPLIED[grounded.reason ?? ""];
    return (
      <Badge title={key ? t(key) : grounded.reason}>{t("code.chat.grounded.notApplied")}</Badge>
    );
  }
  const title = detail(grounded);
  let badge: ReactElement;
  if (grounded.outcome === "supported") {
    badge = <Badge tone="ok" title={title}>{t("code.chat.grounded.verified")}</Badge>;
  } else if (grounded.outcome === "escalated") {
    badge = (
      <Badge tone="ok" title={title}>
        {t("code.chat.grounded.escalated", { model: (grounded.escalated_model ?? "").split("/").pop() ?? "" })}
      </Badge>
    );
  } else if (grounded.outcome === "declined") {
    badge = <Badge tone="warn" title={title}>{t("code.chat.grounded.declined")}</Badge>;
  } else if (grounded.outcome === "lexical") {
    badge = <Badge tone="warn" title={title}>{t("code.chat.grounded.lexical")}</Badge>;
  } else {
    badge = <Badge tone="bad" title={title}>{t("code.chat.grounded.unavailable")}</Badge>;
  }
  const withheld = grounded.withheld ?? [];
  if (!withheld.length) return badge;
  return (
    <>
      {badge}
      <details className="w-full text-xs text-muted-foreground">
        <summary className="cursor-pointer">{t("code.chat.grounded.withheld", { n: withheld.length })}</summary>
        {withheld.map((text, i) => (
          <pre key={i} className="mt-1 whitespace-pre-wrap rounded-chip bg-muted px-2 py-1 font-sans">
            {text}
          </pre>
        ))}
      </details>
    </>
  );
}
