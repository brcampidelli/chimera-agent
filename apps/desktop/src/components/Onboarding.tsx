import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, KeyRound, List, Loader2, X } from "lucide-react";
import { getConfig, patchConfig, testProviderKey } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ModelDialog } from "@/components/code/ModelPicker";
import { useT } from "@/lib/i18n";
import type { ConfigTest } from "@/lib/types";

const inputCls = "field h-9 w-full px-3 text-sm";

const DEFAULT_ENV = "OPENROUTER_API_KEY";

/** First-run setup wizard (the GUI equivalent of `chimera init`). Rendered by App as the whole view
 *  when the doctor reports no provider key. It stays deliberately honest: after Save a key is only
 *  "present"; it says "verified — it works" ONLY after a real test call (POST /api/config/test)
 *  passes. When a key lands, App's doctor query flips `has_any_key` true and this unmounts. */
export function Onboarding({ onSkip }: { onSkip: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const [env, setEnv] = useState(DEFAULT_ENV);
  const [model, setModel] = useState("");
  // Whether the model field is the user's answer or ours. It decides two things: whether switching
  // provider may overwrite the field, and whether the value is worth writing to .env at all.
  const [modelTouched, setModelTouched] = useState(false);
  const [picking, setPicking] = useState(false);
  const [costMode, setCostMode] = useState("auto");
  const [saved, setSaved] = useState(false);
  const [result, setResult] = useState<ConfigTest | null>(null);

  // Everything about a provider — its label, the model to start on, where to get a key — comes from
  // the backend, which is the only place it can be checked. `llm` is the filter that matters:
  // /api/config also lists search, speech and image credentials, and none of those makes
  // `has_any_key` true, so offering one here would take a key, confirm it, and leave this wizard on
  // screen forever waiting for a provider that never arrives.
  const cfg = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const choices = useMemo(() => (cfg.data?.providers ?? []).filter((p) => p.llm), [cfg.data]);
  const pick = choices.find((p) => p.env === env);
  const label = pick?.label ?? "";
  // The suggested slug is DATA and goes stale — the catalog says so about itself. Showing it in an
  // editable field rather than applying it invisibly is what turns a bad suggestion into something
  // the user can see and correct before Save, instead of a failure afterwards.
  const suggested = pick?.model ?? "";
  const shownModel = modelTouched ? model : suggested;

  const pickProvider = (next: string) => {
    setEnv(next);
    setSaved(false);
    setResult(null);
  };

  const saveMutation = useMutation({
    mutationFn: patchConfig,
    onSuccess: () => {
      setSaved(true);
      setResult(null); // a freshly saved key hasn't been verified yet — drop any prior verdict
    },
  });

  const testMutation = useMutation({
    // Test the model this setup will actually use, not the built-in default — which is an OpenRouter
    // slug, and would fail with someone else's 401 while blaming the key just saved.
    mutationFn: () => testProviderKey(shownModel.trim() || undefined),
    onSuccess: (r) => setResult(r),
  });

  const saveKey = () => {
    const updates: Record<string, string> = { [env]: key.trim() };
    // Pin the model unless the user is on OpenRouter and left our suggestion alone — there the
    // built-in default already matches, and writing it would freeze this build's default into their
    // .env forever. Everywhere else the pin is the whole point: the cost presets are all OpenRouter
    // slugs, so an unpinned Anthropic key leaves the tier ladder pointing at a vendor it cannot use.
    if (shownModel.trim() && (modelTouched || env !== DEFAULT_ENV)) {
      updates.CHIMERA_DEFAULT_MODEL = shownModel.trim();
    }
    if (costMode && costMode !== "auto") updates.CHIMERA_COST_MODE = costMode;
    saveMutation.mutate(updates);
  };

  const finish = () => {
    void qc.invalidateQueries({ queryKey: ["doctor"] });
    void qc.invalidateQueries({ queryKey: ["config"] });
  };

  return (
    <div className="flex h-full flex-1 items-center justify-center overflow-y-auto p-6">
      <div className="surface w-full max-w-lg space-y-5 p-6">
        <div className="flex items-center gap-2.5 text-accent">
          <KeyRound className="h-5 w-5" />
          <h1 className="text-lg font-semibold text-foreground">{t("onboarding.title")}</h1>
        </div>

        <p className="text-sm text-muted-foreground">{t("onboarding.intro")}</p>

        {/* Which provider. Only the ones that serve models — /api/config also lists tool keys. */}
        <div className="space-y-2">
          <label htmlFor="ob-provider" className="text-xs font-medium text-muted-foreground">
            {t("onboarding.provider")}
          </label>
          <select
            id="ob-provider"
            className={inputCls}
            value={env}
            onChange={(e) => pickProvider(e.target.value)}
          >
            {choices.map((c) => (
              <option key={c.env} value={c.env}>
                {c.label}
              </option>
            ))}
          </select>
        </div>

        {/* Only when we know where to send them. A provider we merely DISCOVERED (someone's key for
            one of LiteLLM's other hundred vendors) has no sign-up page we can vouch for, and a link
            to nowhere is worse than no link. */}
        {pick?.keys_url ? (
          <a
            href={pick.keys_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-sm text-accent-ink hover:underline"
          >
            {t("onboarding.getKeyLink", { provider: label })}
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        ) : null}

        {/* Key field + Save */}
        <div className="space-y-2">
          <label htmlFor="ob-key" className="text-xs font-medium text-muted-foreground">
            {t("onboarding.keyLabel", { provider: label })}
          </label>
          <input
            id="ob-key"
            className={inputCls}
            type="password"
            autoFocus
            value={key}
            onChange={(e) => {
              setKey(e.target.value);
              setSaved(false);
              setResult(null);
            }}
          />
        </div>

        {/* Optional: default model + cost mode (saved together with the key) */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label htmlFor="ob-model" className="text-xs font-medium text-muted-foreground">
              {t("onboarding.model")}
            </label>
            <div className="flex items-center gap-1.5">
              <input
                id="ob-model"
                className={inputCls}
                value={shownModel}
                onChange={(e) => {
                  setModel(e.target.value);
                  setModelTouched(true);
                  setSaved(false);
                  setResult(null);
                }}
              />
              {/* The field stays — someone who knows the slug should not have to click through to
                  it, and it is what a paste from the docs uses. The list is for everyone else, who
                  otherwise has to guess a slug and find out on the first call.

                  Scoped to the provider selected ABOVE rather than to the keys already configured:
                  on this screen the key has not been saved yet, so filtering by what is configured
                  would answer about a provider the user is in the middle of leaving. */}
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={!pick?.name}
                onClick={() => setPicking(true)}
              >
                <List className="h-4 w-4" />
                {t("onboarding.browseModels")}
              </Button>
            </div>
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground">
              {t("onboarding.costMode")}
            </label>
            <select
              className={inputCls}
              value={costMode}
              onChange={(e) => setCostMode(e.target.value)}
            >
              {["auto", "cheap", "balanced", "premium"].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
        </div>

        <ModelDialog
          open={picking}
          onOpenChange={setPicking}
          value={shownModel}
          provider={pick?.name}
          onPick={(slug) => {
            // "" is the list's way back to the install default, and on this screen that means "keep
            // the suggestion" rather than "clear the field" — a wizard that empties the box because
            // you changed your mind has taken something away.
            if (!slug) return;
            setModel(slug);
            setModelTouched(true);
            setSaved(false);
            setResult(null);
          }}
        />

        <div className="flex items-center gap-2">
          <Button disabled={!key.trim() || saveMutation.isPending} onClick={saveKey}>
            {saveMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            {t("onboarding.save")}
          </Button>
          <Button variant="outline" disabled={!saved || testMutation.isPending} onClick={() => testMutation.mutate()}>
            {testMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            {t("onboarding.test")}
          </Button>
        </div>

        {/* Honest status: "saved (present)" after Save; only "verified — it works" after a passed test. */}
        {result ? (
          result.ok ? (
            <p className="flex items-center gap-1.5 text-sm text-ok-foreground">
              <Check className="h-4 w-4" /> {t("onboarding.verified")}
            </p>
          ) : (
            <p className="flex items-start gap-1.5 text-sm text-bad-foreground">
              <X className="mt-0.5 h-4 w-4 shrink-0" />
              <span>
                {t("onboarding.testFailed")}
                {result.error ? <span className="text-muted-foreground"> — {result.error}</span> : null}
              </span>
            </p>
          )
        ) : saved ? (
          <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Check className="h-4 w-4 text-ok" /> {t("onboarding.saved")}
          </p>
        ) : null}

        <div className="flex items-center justify-between border-t border-hairline pt-4">
          <button className="text-sm text-muted-foreground hover:text-foreground" onClick={onSkip}>
            {t("onboarding.skip")}
          </button>
          <Button disabled={!saved} onClick={finish}>
            {t("onboarding.finish")}
          </Button>
        </div>
      </div>
    </div>
  );
}
