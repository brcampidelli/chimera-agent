import { useState } from "react";
import { ShieldAlert } from "lucide-react";

import { ModelDialog } from "@/components/code/ModelPicker";
import { Button } from "@/components/ui/button";
import type { TFunc } from "@/lib/i18n";
import type { PolicyBlockInfo } from "@/lib/policy-block";

/**
 * A turn the provider refused on content policy, and the one way forward that is the owner's.
 *
 * Study 29 P5.7. The card used to say "That turn failed." over "the coding turn failed" — the same
 * words as a crash in this repository — so a refusal could not be told from a bug, and nothing said
 * which model refused or how to quote the refusal to the provider.
 *
 * **Nothing here is automatic.** The turn was not retried and is not retried until the person picks
 * a model: a model that lets the same text through is, often, a model with weaker safeguards, and
 * routing there on its own is what the study ruled out. The button opens the same model list the
 * composer uses; the pick applies to the retry only, and the conversation's own model is unchanged.
 * The retry's receipt says it was redone, on what, by the owner's choice.
 *
 * The request id is the provider's own, minted by them: not a secret, and what a support desk asks
 * for — so it is shown whole and selectable.
 */
export function PolicyBlocked({
  block,
  current,
  canRetry,
  onRetry,
  t,
}: {
  block: PolicyBlockInfo;
  /** The conversation's model ("" for the install default), selected in the list when it opens. */
  current: string;
  /** False while a turn runs, on any exchange but the last, or when a run holds the project. */
  canRetry: boolean;
  /** Redo the refused turn on `model` ("" = the install default). */
  onRetry: (model: string) => void;
  t: TFunc;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <div className="space-y-1" data-testid="policy-blocked">
      <p className="flex items-start gap-1.5 text-xs text-bad-foreground">
        <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>{t("code.chat.policy.blocked", { model: block.model || "?" })}</span>
      </p>
      {block.provider ? (
        <p className="text-xs text-muted-foreground">
          {t("code.chat.policy.route", { p: block.provider })}
        </p>
      ) : null}
      {block.request_id ? (
        <p className="text-xs text-muted-foreground">
          {t("code.chat.policy.requestId")}{" "}
          <code className="select-all font-mono">{block.request_id}</code>
        </p>
      ) : null}
      <p className="text-xs text-muted-foreground">{t("code.chat.policy.notRetried")}</p>
      {canRetry ? (
        <Button size="sm" variant="ghost" onClick={() => setPicking(true)}>
          {t("code.chat.policy.tryAnother")}
        </Button>
      ) : null}
      <ModelDialog
        open={picking}
        onOpenChange={setPicking}
        value={current}
        onPick={onRetry}
        blurb={t("code.chat.policy.pickBlurb", { model: block.model || "?" })}
      />
    </div>
  );
}
