import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { ErrorState } from "@/components/ui/async";
import { Badge } from "@/components/ui/panel";
import { Switch } from "@/components/ui/switch";
import { getDecisionModels } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";
import type { SystemOneModel } from "@/lib/types";

const BACKEND_KEY = "CHIMERA_DECISION_BACKEND";
const MODEL_KEY = "CHIMERA_DECISION_MODEL";
const VERIFY_KEY = "CHIMERA_VERIFIED_ANSWERS";
const OPENROUTER = "openrouter_decisions";

const BACKENDS = ["local_logprob", "hosted_verbalized", OPENROUTER] as const;
type Backend = (typeof BACKENDS)[number];

function isBackend(value: string): value is Backend {
  return (BACKENDS as readonly string[]).includes(value);
}

/**
 * Which instrument answers a typed decision (`chimera/decisions/factory.py`), and for OpenRouter
 * which System One model.
 *
 * The list is asked only once OpenRouter is the backend: it is a network read, and the two other
 * backends take an Ollama tag or the fusion judge, which this list does not describe. Every row the
 * server sends is shown, choosable or not, because "listed and refused, and why" is information a
 * missing row would hide — someone who read about Span-01 should find it here with its reason.
 *
 * Choosing a backend saves the model empty — its measured default — so switching back to Local can
 * never leave a Jev slug for Ollama to be asked about. The server refuses that pair anyway; the save
 * error line under the screen is where a refusal shows.
 */
export function SystemOneCard({
  backend,
  model,
  verifiedAnswers = true,
  applies,
  onSave,
}: {
  backend: string;
  model: string;
  /** Whether a grounded answer is checked by this backend (`CHIMERA_VERIFIED_ANSWERS`, default on).
   *  A server without the field is on the shipped default, which is on. */
  verifiedAnswers?: boolean;
  applies?: string;
  onSave: (updates: Record<string, string>) => void;
}) {
  const t = useT();
  const headingId = useId();
  const current: Backend = isBackend(backend) ? backend : "local_logprob";
  const choose = (next: Backend) => onSave({ [BACKEND_KEY]: next, [MODEL_KEY]: "" });
  const hints: Record<Backend, [string, string]> = {
    local_logprob: [t("settings.systemOne.local"), t("settings.systemOne.localHint")],
    hosted_verbalized: [t("settings.systemOne.hosted"), t("settings.systemOne.hostedHint")],
    openrouter_decisions: [t("settings.systemOne.openrouter"), t("settings.systemOne.openrouterHint")],
  };

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.systemOne")}
      </h2>
      <div className="divide-y divide-hairline">
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.systemOne.intro")}</p>
        <fieldset className="space-y-2 px-4 py-3">
          <legend className="mb-1 text-sm font-medium">{t("settings.systemOne.backend")}</legend>
          {BACKENDS.map((value) => (
            <label key={value} className="flex cursor-pointer items-start gap-2">
              <input
                type="radio"
                name={`${headingId}-backend`}
                value={value}
                checked={current === value}
                onChange={() => choose(value)}
                className="mt-1"
              />
              <span className="min-w-0">
                <span className="block text-sm">{hints[value][0]}</span>
                <span className="block text-xs text-muted-foreground">{hints[value][1]}</span>
              </span>
            </label>
          ))}
          {applies === "next_conversation" && (
            <div className="text-xs text-warn-foreground">{t("settings.applies.nextConversation")}</div>
          )}
        </fieldset>
        {current === OPENROUTER && <ModelPicker model={model} onSave={onSave} />}
        <VerifyRow on={verifiedAnswers} onSave={onSave} />
      </div>
    </section>
  );
}

/**
 * The one place this instrument is asked on its own on a default install: answers written from
 * attached documents (`chimera/fusion/verified.py`). The measured result and the fallback are said
 * here because they are what someone deciding whether to turn it off needs — what it buys, what it
 * costs, and what runs when the local model does not.
 */
function VerifyRow({ on, onSave }: { on: boolean; onSave: (updates: Record<string, string>) => void }) {
  const t = useT();
  const label = t("settings.systemOne.verify");
  return (
    <div className="space-y-1 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium">{label}</span>
        <Switch
          checked={on}
          label={label}
          onChange={(next) => onSave({ [VERIFY_KEY]: next ? "true" : "false" })}
        />
      </div>
      <div className="text-xs text-muted-foreground">{t("settings.systemOne.verifyHint")}</div>
      <div className="text-xs text-muted-foreground">{t("settings.systemOne.verifyMeasured")}</div>
      <div className="text-xs text-muted-foreground">{t("settings.systemOne.verifyFallback")}</div>
    </div>
  );
}

function price(t: TFunc, m: SystemOneModel): string {
  if (m.input_per_m === null || m.input_per_m === undefined) return t("settings.systemOne.priceUnknown");
  if (m.input_per_m === 0) return t("settings.systemOne.free");
  return t("settings.systemOne.perM", { price: String(m.input_per_m) });
}

function context(t: TFunc, m: SystemOneModel): string {
  if (!m.context) return t("settings.systemOne.contextUnknown");
  return t("settings.systemOne.context", { k: String(Math.round(m.context / 1000)) });
}

function refusal(t: TFunc, reason: string): string {
  // Literal keys, one per word the server sends, so the reachability test can see each of them.
  if (reason === "alias") return t("settings.systemOne.refusal.alias");
  if (reason === "behavior_contract") return t("settings.systemOne.refusal.behavior");
  return t("settings.systemOne.refusal.unknown");
}

function ModelPicker({
  model,
  onSave,
}: {
  model: string;
  onSave: (updates: Record<string, string>) => void;
}) {
  const t = useT();
  const selectId = useId();
  const q = useQuery({ queryKey: ["decision-models"], queryFn: () => getDecisionModels() });

  if (q.isError) {
    return (
      <div className="px-4 py-3">
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      </div>
    );
  }
  if (q.isLoading || !q.data) {
    return (
      <div className="flex items-center px-4 py-3 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
      </div>
    );
  }
  const data = q.data;
  const active = model || data.default_model;
  const choosable = data.models.filter((m) => m.selectable);
  const refused = data.models.filter((m) => !m.selectable);
  const chosen = data.models.find((m) => m.slug === active);

  return (
    <div className="space-y-2 px-4 py-3">
      <label htmlFor={selectId} className="block text-sm font-medium">
        {t("settings.systemOne.model")}
      </label>
      <select
        id={selectId}
        className="field h-8 w-full px-2.5 text-sm"
        value={active}
        onChange={(e) => onSave({ [BACKEND_KEY]: OPENROUTER, [MODEL_KEY]: e.target.value })}
      >
        {/* A saved slug the list no longer carries (offline, or withdrawn) stays visible as the
            value rather than the select silently showing its first option as if it were chosen. */}
        {!chosen && <option value={active}>{active}</option>}
        {choosable.map((m) => (
          <option key={m.slug} value={m.slug}>
            {`${m.name} · ${price(t, m)} · ${context(t, m)} · ${
              m.calibrated ? t("settings.systemOne.calibrated") : t("settings.systemOne.uncalibrated")
            }`}
          </option>
        ))}
      </select>
      {chosen && (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">{chosen.slug}</span>
            <span>{price(t, chosen)}</span>
            <span>{context(t, chosen)}</span>
            <Badge tone={chosen.calibrated ? "ok" : "warn"}>
              {chosen.calibrated ? t("settings.systemOne.calibrated") : t("settings.systemOne.uncalibrated")}
            </Badge>
          </div>
          {!chosen.calibrated && (
            <div className="text-xs text-warn-foreground">{t("settings.systemOne.uncalibratedNote")}</div>
          )}
          {chosen.description && <div className="text-xs text-muted-foreground">{chosen.description}</div>}
        </div>
      )}
      {data.stale && <div className="text-xs text-warn-foreground">{t("settings.systemOne.stale")}</div>}
      {!data.openrouter_key_set && (
        <div className="text-xs text-warn-foreground">{t("settings.systemOne.noKey")}</div>
      )}
      {refused.length > 0 && (
        <div className="text-xs text-muted-foreground">
          <div>{t("settings.systemOne.notChoosable")}</div>
          <ul className="ml-4 list-disc">
            {refused.map((m) => (
              <li key={m.slug}>
                <span className="font-mono">{m.slug}</span> — {refusal(t, m.refusal ?? "")}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
