import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/async";
import { Panel, Spinner } from "@/components/ui/panel";
import {
  closeNetworkShare,
  getAccess,
  revokeAccessLink,
  revokeAllAccessLinks,
  rotateBridgeToken,
} from "@/lib/api";
import { useI18n, useT, type TFunc } from "@/lib/i18n";
import type { AccessLink, AccessState } from "@/lib/types";

const ACCESS_KEY = ["security-access"];

/** A moment in the APP's language, not the operating system's (`numbers-follow-the-language`). */
function when(seconds: number, lang: string): string {
  return new Date(seconds * 1000).toLocaleString(lang);
}

/** One door: what it is, its state in a sentence, and the one control that narrows it. ``warn`` is
 *  a door that is open right now — said in the warn colour, not in red: an open door the owner chose
 *  is not an error, and red on every visit would teach them to stop reading it. */
function DoorRow({
  label,
  state,
  warn = false,
  action,
}: {
  label: string;
  state: string;
  warn?: boolean;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        <div className={warn ? "text-xs text-warn-foreground" : "text-xs text-muted-foreground"}>
          {state}
        </div>
      </div>
      {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
    </div>
  );
}

function LinkRow({
  link,
  t,
  lang,
  busy,
  onRevoke,
}: {
  link: AccessLink;
  t: TFunc;
  lang: string;
  busy: boolean;
  onRevoke: () => void;
}) {
  const expiry = link.expired
    ? t("governance.access.expired")
    : link.expires_at
      ? t("governance.access.expires", { when: when(link.expires_at, lang) })
      : t("governance.access.never");
  return (
    <div className="flex items-center gap-3 px-4 py-2.5" data-testid="access-link">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-foreground">
          {link.label || t("governance.access.unlabeled")}
          <span className="text-muted-foreground"> · </span>
          <span className="text-muted-foreground">
            {link.session_title || t("governance.access.noTitle")}
          </span>
        </div>
        <div className="text-xs text-muted-foreground">
          {t("governance.access.created", { when: when(link.created_at, lang) })} · {expiry}
        </div>
      </div>
      {/* The last four characters and never more: enough to tell two links of one label apart,
          which is the only reason the card shows anything of the token at all. */}
      <span className="shrink-0 font-mono text-xs text-muted-foreground">{link.hint}</span>
      <Button size="sm" variant="outline" disabled={busy} onClick={onRevoke}>
        {t("governance.access.revoke")}
      </Button>
    </div>
  );
}

function bridgeState(data: AccessState["bridge"], t: TFunc): string {
  if (data.active) {
    const tier =
      data.tier === "full" ? t("governance.access.tierFull") : t("governance.access.tierOperate");
    return t("governance.access.bridgeOn", { tier, hint: data.hint });
  }
  return data.enabled ? t("governance.access.bridgeWaiting") : t("governance.access.bridgeOff");
}

/** The network door in a sentence. Two listeners can answer the network with a share link: the LAN
 *  door opened from a Share dialog, and the app's own listener when `chimera desktop --host` bound
 *  it to a network address — in which case the guest app at `/guest` is on the network too, and
 *  "Closed" would be the one false thing on a card about every way in. */
function doorState(data: AccessState, t: TFunc): string {
  if (data.guest_door.open) {
    return t("governance.access.doorOpen", { port: data.guest_door.port ?? "" });
  }
  if (data.server.network) {
    const address = data.server.port ? `${data.server.bind}:${data.server.port}` : data.server.bind;
    return t("governance.access.doorAppOnNetwork", { address: address ?? "" });
  }
  return t("governance.access.doorClosed");
}

function sharingState(data: AccessState["sharing"], t: TFunc): string {
  if (!data.enabled) return t("governance.access.sharingOff");
  return data.expiry_hours
    ? t("governance.access.sharingOnExpiry", { hours: data.expiry_hours })
    : t("governance.access.sharingOn");
}

/**
 * Every way into this machine, on one card (`GET /api/security/access`).
 *
 * The doors existed and were scattered: the bearer on the General tab, the bridge's token visible
 * nowhere, each share link inside its own conversation's Share dialog, the LAN door inside that same
 * dialog. Nothing here opens a door. Every control narrows — revoke, close, a new bridge token — and
 * the bearer and the two sharing settings are changed where they live, on the General tab, which
 * `onOpenSettings` takes the person to.
 */
export function AccessCard({ onOpenSettings }: { onOpenSettings?: () => void }) {
  const t = useT();
  const { lang } = useI18n();
  const qc = useQueryClient();
  // Probed on every open, like the sandbox above it: a link a guest was given an hour ago, or a door
  // someone opened from another window, has to show now, not from a cache.
  const access = useQuery({ queryKey: ACCESS_KEY, queryFn: getAccess, staleTime: 0, gcTime: 0 });
  const [rotated, setRotated] = useState(false);
  const refresh = () => void qc.invalidateQueries({ queryKey: ACCESS_KEY });
  const rotate = useMutation({
    // Wrapped, so the client functions are reached only when a button is pressed — a screen that
    // mounts this card does not have to stand up every control's endpoint to render it.
    mutationFn: () => rotateBridgeToken(),
    onSuccess: () => {
      setRotated(true);
      refresh();
    },
  });
  const revokeOne = useMutation({
    mutationFn: (id: string) => revokeAccessLink(id),
    onSuccess: refresh,
  });
  const revokeAll = useMutation({ mutationFn: () => revokeAllAccessLinks(), onSuccess: refresh });
  const closeDoor = useMutation({ mutationFn: () => closeNetworkShare(), onSuccess: refresh });

  const title = t("governance.access.title");
  if (access.isError) {
    return (
      <Panel title={title}>
        <ErrorState error={access.error} onRetry={() => access.refetch()} />
      </Panel>
    );
  }
  if (access.isLoading || !access.data) {
    return (
      <Panel title={title}>
        <Spinner />
      </Panel>
    );
  }
  const data = access.data;
  const busy = revokeOne.isPending || revokeAll.isPending;
  const change = onOpenSettings ? (
    <Button size="sm" variant="outline" onClick={onOpenSettings}>
      {t("governance.access.change")}
    </Button>
  ) : undefined;

  return (
    <Panel title={title}>
      <p className="flex items-start gap-2 px-4 py-3 text-xs text-muted-foreground">
        <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {t("governance.access.intro")}
      </p>
      <DoorRow
        label={t("governance.access.serverToken")}
        state={
          data.server_token.set
            ? t("governance.access.serverTokenSet")
            : t("governance.access.serverTokenUnset")
        }
        warn={!data.server_token.set}
        action={change}
      />
      <DoorRow
        label={t("governance.access.bridge")}
        state={rotated && data.bridge.active ? t("governance.access.bridgeRotated") : bridgeState(data.bridge, t)}
        warn={data.bridge.active}
        action={
          <Button
            size="sm"
            variant="outline"
            disabled={!data.bridge.active || rotate.isPending}
            onClick={() => rotate.mutate()}
          >
            {t("governance.access.bridgeRotate")}
          </Button>
        }
      />
      <DoorRow
        label={t("governance.access.door")}
        state={doorState(data, t)}
        warn={data.guest_door.open || data.server.network}
        action={
          data.guest_door.open ? (
            <Button
              size="sm"
              variant="outline"
              disabled={closeDoor.isPending}
              onClick={() => closeDoor.mutate()}
            >
              {t("governance.access.doorClose")}
            </Button>
          ) : undefined
        }
      />
      <DoorRow
        label={t("governance.access.sharing")}
        state={sharingState(data.sharing, t)}
        action={change}
      />
      <div className="flex items-center justify-between gap-4 px-4 pb-1 pt-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t("governance.access.links", { n: data.links.length })}
        </span>
        {data.links.length > 0 ? (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => revokeAll.mutate()}>
            {t("governance.access.revokeAll")}
          </Button>
        ) : null}
      </div>
      {data.links.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">{t("governance.access.linksNone")}</p>
      ) : (
        data.links.map((link) => (
          <LinkRow
            key={link.id}
            link={link}
            t={t}
            lang={lang}
            busy={busy}
            onRevoke={() => revokeOne.mutate(link.id)}
          />
        ))
      )}
    </Panel>
  );
}
