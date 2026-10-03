import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";

import { ModelDialog } from "@/components/code/ModelPicker";
import { Button } from "@/components/ui/button";
import { getModels } from "@/lib/api";
import type { TFunc } from "@/lib/i18n";
import type { PolicyBlockInfo } from "@/lib/policy-block";

/** Matches no row of the model list, "default" included: see the dialog below. */
const NOTHING_CHOSEN = "(nothing chosen)";

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
 *
 * The list opens with NOTHING selected, and a pick that resolves to the model that refused (its
 * slug, or "default" when the default is that model) asks before it sends. The list opened on the
 * conversation's model, which is usually the one that refused, and a click on a row sends at once:
 * one click on the highlighted row paid for the same refusal again, and the receipt then read
 * "blocked on X, redone on X by the owner's choice".
 */
export function PolicyBlocked({
  block,
  canRetry,
  filesMissing = false,
  onRetry,
  t,
}: {
  block: PolicyBlockInfo;
  /** False while a turn runs, on any exchange but the last, or when a run holds the project. */
  canRetry: boolean;
  /** The turn's files are not known on this screen (it was followed, not sent from here). A retry
   *  would go out without them and answer a different question, so it is not offered; the card
   *  says what to do instead. */
  filesMissing?: boolean;
  /** Redo the refused turn on `model` ("" = the install default). */
  onRetry: (model: string) => void;
  t: TFunc;
}) {
  const [picking, setPicking] = useState(false);
  /** A pick that is the model that refused, held until the owner says to send it anyway. */
  const [same, setSame] = useState<string | null>(null);
  // The same query the dialog runs (same key, same function), read here for the one thing it
  // knows that this card does not: which slug "default" stands for.
  const listing = useQuery({
    queryKey: ["models", ""],
    queryFn: () => getModels(),
    enabled: picking,
    staleTime: 5 * 60 * 1000,
  });
  const pick = (slug: string) => {
    const resolved = slug || listing.data?.default || "";
    if (block.model && resolved === block.model) setSame(slug);
    else onRetry(slug);
  };
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
      {canRetry && filesMissing ? (
        <p className="text-xs text-warn-foreground" data-testid="policy-files-missing">
          {t("code.chat.policy.filesMissing")}
        </p>
      ) : null}
      {canRetry && !filesMissing && same === null ? (
        <Button size="sm" variant="ghost" onClick={() => setPicking(true)}>
          {t("code.chat.policy.tryAnother")}
        </Button>
      ) : null}
      {canRetry && !filesMissing && same !== null ? (
        <div className="space-y-1" data-testid="policy-same-model">
          <p className="text-xs text-warn-foreground">
            {t("code.chat.policy.sameModel", { model: block.model })}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                const slug = same;
                setSame(null);
                onRetry(slug);
              }}
            >
              {t("code.chat.policy.sameModelGo")}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSame(null)}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>
      ) : null}
      <ModelDialog
        open={picking}
        onOpenChange={setPicking}
        // No slug has a space or a bracket, so nothing is highlighted, the "default" row included.
        // A retry is a fresh choice, and the row that used to be highlighted was most often the
        // one that refused.
        value={NOTHING_CHOSEN}
        onPick={pick}
        blurb={t("code.chat.policy.pickBlurb", { model: block.model || "?" })}
      />
    </div>
  );
}
