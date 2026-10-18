import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Panel, Spinner } from "@/components/ui/panel";
import { Switch } from "@/components/ui/switch";
import { getBridgeFullControl, setBridgeFullControl } from "@/lib/api";
import { useI18n, useT, type TFunc } from "@/lib/i18n";
import type { BridgeFullControlAction } from "@/lib/types";

/** Shared with the banner, so a change on either side redraws the other. */
export const FULL_CONTROL_KEY = ["bridge-full-control"];

/** A moment in the APP's language, not the operating system's (`numbers-follow-the-language`). */
function when(iso: string, lang: string): string {
  const at = Date.parse(iso);
  return Number.isNaN(at) ? iso : new Date(at).toLocaleString(lang);
}

/** One audited line: the route and its ids, or the switch itself. Never content — the backend
 *  records none (`chimera/api/bridge_full_control.py`). */
function ActionRow({ action, t, lang }: { action: BridgeFullControlAction; t: TFunc; lang: string }) {
  const switched = action.event === "bridge.full_control.switch";
  const ids = Object.entries(action.ids ?? {})
    .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : String(v)}`)
    .join(" ");
  return (
    <div className="flex items-center gap-3 px-4 py-2" data-testid="full-control-action">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-foreground">
          {switched
            ? action.on
              ? t("governance.fullControl.switchedOn")
              : t("governance.fullControl.switchedOff")
            : action.route}
          {ids ? <span className="font-mono text-xs text-muted-foreground"> {ids}</span> : null}
        </div>
        <div className="text-xs text-muted-foreground">
          {when(action.at ?? "", lang)}
          {!switched && action.status != null
            ? ` · ${t("governance.fullControl.status", { status: action.status })}`
            : ""}
        </div>
      </div>
    </div>
  );
}

/**
 * Full control through the bridge, on the Security tab (owner's decision, 2026-10-18).
 *
 * The one place it is switched ON, and only after a confirmation that says what it hands over. The
 * backend refuses the ON without this screen's credential, so the card is not a convenience over a
 * route anyone could call — it is the only door. Below the switch, the newest calls made on its
 * authority, from the audit log: route and ids, never content.
 */
export function BridgeFullControlCard() {
  const t = useT();
  const { lang } = useI18n();
  const qc = useQueryClient();
  const state = useQuery({ queryKey: FULL_CONTROL_KEY, queryFn: getBridgeFullControl, staleTime: 0 });
  const [confirming, setConfirming] = useState(false);
  const change = useMutation({
    mutationFn: (on: boolean) => setBridgeFullControl(on),
    onSuccess: (next) => {
      qc.setQueryData(FULL_CONTROL_KEY, next);
      setConfirming(false);
    },
  });

  const title = t("governance.fullControl.title");
  if (state.isError) {
    return (
      <Panel title={title}>
        <ErrorState error={state.error} onRetry={() => state.refetch()} />
      </Panel>
    );
  }
  if (state.isLoading || !state.data) {
    return (
      <Panel title={title}>
        <Spinner />
      </Panel>
    );
  }
  const data = state.data;
  const actions = data.actions ?? [];
  return (
    <Panel title={title}>
      <p className="flex items-start gap-2 px-4 py-3 text-xs text-muted-foreground">
        <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        {t("governance.fullControl.intro")}
      </p>
      <div className="flex items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          <div className="text-sm font-medium">{t("governance.fullControl.switch")}</div>
          <div
            className={
              data.on ? "text-xs text-warn-foreground" : "text-xs text-muted-foreground"
            }
          >
            {data.available ? t("governance.fullControl.hint") : t("governance.fullControl.needsFull")}
          </div>
        </div>
        <Switch
          checked={data.on}
          disabled={change.isPending || (!data.on && !data.available)}
          label={t("governance.fullControl.switch")}
          // On asks first; off is one click, because it only narrows.
          onChange={(next) => (next ? setConfirming(true) : change.mutate(false))}
        />
      </div>
      {change.isError ? (
        <p className="px-4 py-2 text-xs text-bad-foreground">
          {t("governance.fullControl.error", {
            error: change.error instanceof Error ? change.error.message : String(change.error),
          })}
        </p>
      ) : null}
      <div className="px-4 pb-1 pt-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t("governance.fullControl.actions", { n: actions.length })}
        </span>
      </div>
      {actions.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          {t("governance.fullControl.actionsNone")}
        </p>
      ) : (
        actions.map((action, i) => (
          <ActionRow key={`${action.at}-${i}`} action={action} t={t} lang={lang} />
        ))
      )}
      <Dialog
        open={confirming}
        onOpenChange={(next) => !next && setConfirming(false)}
        title={t("governance.fullControl.confirmTitle")}
      >
        <p className="text-sm text-muted-foreground">{t("governance.fullControl.confirmBody")}</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirming(false)}>
            {t("common.cancel")}
          </Button>
          <Button size="sm" disabled={change.isPending} onClick={() => change.mutate(true)}>
            {t("governance.fullControl.confirm")}
          </Button>
        </div>
      </Dialog>
    </Panel>
  );
}
