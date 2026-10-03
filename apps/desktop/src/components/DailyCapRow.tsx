import { useEffect, useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { getConfig, patchConfig } from "@/lib/api";
import { useT } from "@/lib/i18n";

const CAP_KEY = "CHIMERA_DAILY_USD_CAP";

/**
 * The day's dollar ceiling (`CHIMERA_DAILY_USD_CAP`), at the top of Cost & Usage.
 *
 * It existed and braked the scheduler with no screen, so the only way to bound what unattended jobs
 * spend was a line in `.env`. The hint says what it covers in the plainest words available —
 * scheduled tasks only — because "daily cap" on a usage screen reads as a ceiling on everything, and
 * a chat that runs past a cap the person believed in is the trust this row would spend. Extending it
 * to chat and Code is a behaviour change, not a label change, and it is not made here.
 *
 * Empty is "no cap". The server refuses zero (the scheduler reads the cap with `if cap`, so zero
 * would brake nothing) and anything that is not a positive amount; the refusal shows under the row.
 */
export function DailyCapRow() {
  const t = useT();
  const inputId = useId();
  const hintId = useId();
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["config"], queryFn: () => getConfig() });
  const saved = config.data?.spend?.daily_usd_cap ?? null;
  const [draft, setDraft] = useState("");
  useEffect(() => {
    setDraft(saved === null ? "" : String(saved));
  }, [saved]);
  const mutation = useMutation({
    mutationFn: (value: string) => patchConfig({ [CAP_KEY]: value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });
  const dirty = draft.trim() !== (saved === null ? "" : String(saved));

  return (
    <div className="surface space-y-1.5 px-4 py-3">
      <form
        className="flex flex-wrap items-center gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          mutation.mutate(draft.trim());
        }}
      >
        <label htmlFor={inputId} className="text-sm font-medium">
          {t("usage.dailyCap")}
        </label>
        <span className="flex items-center gap-1 text-sm text-muted-foreground">
          $
          <input
            id={inputId}
            inputMode="decimal"
            value={draft}
            placeholder={t("usage.dailyCap.none")}
            aria-describedby={hintId}
            onChange={(e) => setDraft(e.target.value)}
            className="field h-8 w-28 px-2.5 text-sm text-foreground"
          />
        </span>
        <Button type="submit" size="sm" variant="outline" disabled={!dirty || mutation.isPending}>
          {t("common.save")}
        </Button>
      </form>
      <p id={hintId} className="text-xs text-muted-foreground">
        {t("usage.dailyCap.hint")}
      </p>
      {mutation.isError && (
        <p role="alert" className="text-xs text-bad-foreground">
          {mutation.error instanceof Error ? mutation.error.message : String(mutation.error)}
        </p>
      )}
    </div>
  );
}
