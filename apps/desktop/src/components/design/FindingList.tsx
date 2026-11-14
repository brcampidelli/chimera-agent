import type { DesignFinding, DesignUnchecked } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Keys written out (the i18n reachability check reads literals). The server's `kind` codes are
 *  `check_against_tokens`' (chimera/design/export_html.py); an unknown one shows the server's text. */
const KIND_KEY: Record<string, string> = {
  color_not_in_tokens: "design.check.kind.colour",
  color_token_missing: "design.check.kind.colourMissing",
  font_not_from_tokens: "design.check.kind.font",
  low_contrast: "design.check.kind.contrast",
  outside_artboard: "design.check.kind.outside",
  spacing_not_in_tokens: "design.check.kind.spacing",
};

/** Token checks, each with a way to the element it is about. */
export function FindingList({
  findings,
  onJump,
}: {
  findings: readonly DesignFinding[];
  onJump: (ids: string[]) => void;
}) {
  const t = useT();
  if (findings.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2" aria-label={t("design.check.title")}>
      {findings.map((f, i) => (
        <li key={i} className="flex items-start gap-2 text-sm">
          <span className="min-w-0 flex-1 break-words">
            {KIND_KEY[f.kind] ? <span className="font-medium text-foreground">{t(KIND_KEY[f.kind])}: </span> : null}
            <span className="text-muted-foreground">{f.message}</span>
          </span>
          {f.node_id ? (
            <button
              type="button"
              className="shrink-0 text-xs text-accent-ink underline decoration-dotted"
              onClick={() => onJump([f.node_id as string])}
            >
              {t("design.check.jump")}
            </button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** What the checks did not cover, always shown apart from the findings: "not checked" is not "clean". */
export function UncheckedList({ unchecked }: { unchecked: readonly DesignUnchecked[] }) {
  const t = useT();
  if (unchecked.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <h4 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("design.check.unchecked.title")}</h4>
      <ul className="flex flex-col gap-1" aria-label={t("design.check.unchecked.title")}>
        {unchecked.map((u, i) => (
          <li key={i} className="break-words text-sm text-muted-foreground">
            {u.message}
          </li>
        ))}
      </ul>
    </div>
  );
}
