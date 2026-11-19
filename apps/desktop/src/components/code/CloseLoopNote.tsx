import type { CodeTurnDone } from "@/lib/api";
import { closeLoopPresentation, closeLoopPrHref } from "@/lib/closeLoopNote";
import { useT } from "@/lib/i18n";

/** Reasons and diagnostics are literal React text. Only the explicit PR field may become a link. */
export function CloseLoopNote({ done }: { done: CodeTurnDone | null }) {
  const t = useT();
  const content = closeLoopPresentation(done, t);
  if (!content || (!content.text && !content.prUrl)) return null;
  const href = closeLoopPrHref(content.prUrl);
  return (
    <div className="space-y-1 whitespace-pre-wrap text-sm text-foreground/90" data-testid="close-loop-note">
      {content.text ? <p>{content.text}</p> : null}
      {content.diagnostic ? <p>{content.diagnostic}</p> : null}
      {content.prUrl ? <p>{href ? (
        <a href={href} target="_blank" rel="noreferrer" className="underline">
          {t("code.closeLoop.pullRequest", { url: content.prUrl })}
        </a>
      ) : t("code.closeLoop.pullRequest", { url: content.prUrl })}</p> : null}
    </div>
  );
}
