import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, KeyRound, Loader2, X } from "lucide-react";
import { patchConfig, testProviderKey } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import type { ConfigTest } from "@/lib/types";

const inputCls = "field h-9 w-full px-3 text-sm";

/** First-run setup wizard (the GUI equivalent of `chimera init`). Rendered by App as the whole view
 *  when the doctor reports no provider key. It stays deliberately honest: after Save a key is only
 *  "present"; it says "verified — it works" ONLY after a real test call (POST /api/config/test)
 *  passes. When a key lands, App's doctor query flips `has_any_key` true and this unmounts. */
export function Onboarding({ onSkip }: { onSkip: () => void }) {
  const t = useT();
  const qc = useQueryClient();
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [costMode, setCostMode] = useState("auto");
  const [saved, setSaved] = useState(false);
  const [result, setResult] = useState<ConfigTest | null>(null);

  const saveMutation = useMutation({
    mutationFn: patchConfig,
    onSuccess: () => {
      setSaved(true);
      setResult(null); // a freshly saved key hasn't been verified yet — drop any prior verdict
    },
  });

  const testMutation = useMutation({
    mutationFn: () => testProviderKey(model.trim() || undefined),
    onSuccess: (r) => setResult(r),
  });

  const saveKey = () => {
    const updates: Record<string, string> = { OPENROUTER_API_KEY: key.trim() };
    if (model.trim()) updates.CHIMERA_DEFAULT_MODEL = model.trim();
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
        <a
          href="https://openrouter.ai/keys"
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
        >
          {t("onboarding.getKeyLink")}
          <ExternalLink className="h-3.5 w-3.5" />
        </a>

        {/* Key field + Save */}
        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground">
            {t("onboarding.keyLabel")}
          </label>
          <input
            className={inputCls}
            type="password"
            autoFocus
            placeholder="sk-or-…"
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
            <label className="text-xs font-medium text-muted-foreground">
              {t("onboarding.model")}
            </label>
            <input
              className={inputCls}
              placeholder="openrouter/…"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
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
            <p className="flex items-center gap-1.5 text-sm text-ok">
              <Check className="h-4 w-4" /> {t("onboarding.verified")}
            </p>
          ) : (
            <p className="flex items-start gap-1.5 text-sm text-bad">
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

        <div className="flex items-center justify-between border-t border-white/5 pt-4">
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
