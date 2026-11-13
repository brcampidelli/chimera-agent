import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  listBrowserScreenSessions,
  mintBrowserScreenCode,
  revokeAllBrowserScreenSessions,
  revokeBrowserScreenSession,
} from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";
import type { BrowserScreenCode, BrowserScreenSession } from "@/lib/types";
import { setVisibleInterval } from "@/lib/visibleInterval";

const SESSIONS_KEY = ["browser-screen-sessions"] as const;

/** A moment in the APP's language, not the operating system's (`numbers-follow-the-language`). */
function when(seconds: number, lang: string): string {
  return new Date(seconds * 1000).toLocaleString(lang);
}

/** Whole seconds left until `until` (epoch seconds), ticking once a second while there is a code. */
function useSecondsLeft(until: number | null): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (until === null) return undefined;
    setNow(Date.now() / 1000);
    return setVisibleInterval(() => setNow(Date.now() / 1000), 1000);
  }, [until]);
  return until === null ? 0 : Math.max(0, Math.ceil(until - now));
}

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60);
  return `${m}:${String(seconds % 60).padStart(2, "0")}`;
}

function SessionRow({
  session,
  lang,
  busy,
  onRevoke,
}: {
  session: BrowserScreenSession;
  lang: string;
  busy: boolean;
  onRevoke: () => void;
}) {
  const t = useT();
  return (
    <div className="flex items-center gap-3 px-4 py-2.5" data-testid="browser-pairing-session">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-foreground">{session.name}</div>
        <div className="text-xs text-muted-foreground">
          {t("browserPairing.session.meta", {
            created: when(session.created_at, lang),
            used: when(session.last_used_at, lang),
            until: when(session.expires_at, lang),
          })}
        </div>
      </div>
      <Button size="sm" variant="outline" disabled={busy} onClick={onRevoke}>
        {t("governance.access.revoke")}
      </Button>
    </div>
  );
}

/**
 * Browser pairing, on the Security screen (`chimera/api/browser_screen_api.py`).
 *
 * Only the desktop app can make a code or change the list, so the controls are drawn only in the
 * desktop window. In a browser tab the card says so and does nothing else: a control that the server
 * would refuse is a control that fails silently, which is the failure this feature exists to end.
 * The code is shown once, from the response that made it, and counts down until it is spent or stale.
 */
export function BrowserScreenPairing() {
  const t = useT();
  const { lang } = useI18n();
  const qc = useQueryClient();
  const native = typeof window !== "undefined" && Boolean(window.__CHIMERA_SCREEN_TOKEN__);
  const [code, setCode] = useState<BrowserScreenCode | null>(null);
  const left = useSecondsLeft(code ? code.expires_at : null);

  const sessions = useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: () => listBrowserScreenSessions(),
    enabled: native,
    staleTime: 0,
    gcTime: 0,
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: SESSIONS_KEY });
  const mint = useMutation({ mutationFn: () => mintBrowserScreenCode(), onSuccess: setCode });
  const revokeOne = useMutation({ mutationFn: (id: string) => revokeBrowserScreenSession(id), onSuccess: refresh });
  const revokeAll = useMutation({ mutationFn: () => revokeAllBrowserScreenSessions(), onSuccess: refresh });
  const busy = revokeOne.isPending || revokeAll.isPending;
  const list = sessions.data?.sessions ?? [];

  return (
    <div id="settings-browser-pairing" className="border-t border-hairline px-4 py-3" data-testid="browser-pairing">
      <div className="text-sm font-medium">{t("browserPairing.title")}</div>
      <p className="text-xs text-muted-foreground">{t("browserPairing.lead")}</p>
      {!native ? (
        <p className="pt-2 text-xs text-muted-foreground">{t("browserPairing.browserNote")}</p>
      ) : (
        <>
          <div className="flex items-center justify-between gap-4 pt-2">
            <p className="text-xs text-muted-foreground">{t("browserPairing.codeHint")}</p>
            <Button size="sm" disabled={mint.isPending} onClick={() => mint.mutate()}>
              {t("browserPairing.show")}
            </Button>
          </div>
          {code && left > 0 ? (
            <div className="pt-3" data-testid="browser-pairing-code">
              <div className="font-mono text-xl tracking-widest text-foreground">{code.code}</div>
              <p className="text-xs text-muted-foreground">
                {t("browserPairing.countdown", { time: clock(left) })}
              </p>
            </div>
          ) : null}
          <div className="flex items-center justify-between gap-4 pb-1 pt-3">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {t("browserPairing.list", { n: list.length })}
            </span>
            {list.length > 0 ? (
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => revokeAll.mutate()}>
                {t("governance.access.revokeAll")}
              </Button>
            ) : null}
          </div>
          {list.length === 0 ? (
            <p className="py-2 text-xs text-muted-foreground">{t("browserPairing.none")}</p>
          ) : (
            list.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                lang={lang}
                busy={busy}
                onRevoke={() => revokeOne.mutate(session.id)}
              />
            ))
          )}
        </>
      )}
    </div>
  );
}
