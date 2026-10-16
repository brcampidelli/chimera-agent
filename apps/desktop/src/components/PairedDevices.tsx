import { useMutation } from "@tanstack/react-query";
import { Smartphone } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  closeDeviceDoor,
  newPairingCode,
  openDeviceDoor,
  revokeAllDevices,
  revokeDevice,
} from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";
import type { AccessDevice, AccessState, PairCode } from "@/lib/types";

/** A moment in the APP's language, not the operating system's (`numbers-follow-the-language`). */
function when(seconds: number, lang: string): string {
  return new Date(seconds * 1000).toLocaleString(lang);
}

/**
 * The QR code of a pairing address, drawn from the module rows the backend computed
 * (`chimera/core/qr.py`) — one path, no library. Always dark on light, whatever the theme: a camera
 * reads dark modules on a light ground, and an inverted code is one some readers refuse. The light
 * ground is the light theme's own `--background`, reached through `.theme-scope` (index.css), so no
 * colour is written here. Four modules of quiet zone, as the standard asks.
 */
export function QrCode({ rows, label }: { rows: readonly string[]; label: string }) {
  const size = rows.length;
  if (size === 0) return null;
  const quiet = 4;
  let d = "";
  rows.forEach((row, y) => {
    for (let x = 0; x < row.length; x++) {
      if (row[x] === "1") d += `M${x + quiet} ${y + quiet}h1v1h-1z`;
    }
  });
  return (
    <div
      className="theme-scope inline-block rounded-md bg-background p-1 text-foreground"
      data-theme-scope="light"
    >
      <svg
        role="img"
        aria-label={label}
        viewBox={`0 0 ${size + quiet * 2} ${size + quiet * 2}`}
        className="h-44 w-44"
        shapeRendering="crispEdges"
      >
        <path d={d} fill="currentColor" />
      </svg>
    </div>
  );
}

function DeviceRow({
  device,
  lang,
  busy,
  onRevoke,
}: {
  device: AccessDevice;
  lang: string;
  busy: boolean;
  onRevoke: () => void;
}) {
  const t = useT();
  const used = device.last_ip
    ? t("governance.access.devicesUsed", { when: when(device.last_used_at, lang), ip: device.last_ip })
    : t("governance.access.devicesUsedNoIp", { when: when(device.last_used_at, lang) });
  return (
    <div className="flex items-center gap-3 px-4 py-2.5" data-testid="access-device">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm text-foreground">{device.name}</div>
        <div className="text-xs text-muted-foreground">{used}</div>
      </div>
      <Button size="sm" variant="outline" disabled={busy} onClick={onRevoke}>
        {t("governance.access.revoke")}
      </Button>
    </div>
  );
}

/**
 * The owner's own phones and browsers (S3, C10): the LAN listener they open the app through, the
 * one-time code that pairs one, and the list with a revoke on every row.
 *
 * The listener is opened here and nowhere else on this card: it is the one control that WIDENS, so it
 * is a button the owner presses each time (it is shut at every start), never a remembered switch. The
 * code is shown once, from the response that made it, and never fetched again.
 */
export function PairedDevices({
  door,
  devices,
  onChanged,
}: {
  door: AccessState["device_door"];
  devices: readonly AccessDevice[];
  onChanged: () => void;
}) {
  const t = useT();
  const { lang } = useI18n();
  const [code, setCode] = useState<PairCode | null>(null);
  const open = useMutation({ mutationFn: () => openDeviceDoor(), onSuccess: onChanged });
  const close = useMutation({
    mutationFn: () => closeDeviceDoor(),
    onSuccess: () => {
      // A code is for the door it was made for: closing it voided the code on the server too.
      setCode(null);
      onChanged();
    },
  });
  const pair = useMutation({ mutationFn: () => newPairingCode(), onSuccess: setCode });
  const revokeOne = useMutation({ mutationFn: (id: string) => revokeDevice(id), onSuccess: onChanged });
  const revokeAll = useMutation({ mutationFn: () => revokeAllDevices(), onSuccess: onChanged });
  const busy = revokeOne.isPending || revokeAll.isPending;
  const isOpen = Boolean(door?.open);

  return (
    <>
      <div className="flex items-center justify-between gap-4 border-t border-hairline px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 text-sm font-medium">
            <Smartphone className="h-3.5 w-3.5 shrink-0" />
            {t("governance.access.devicesDoor")}
          </div>
          <div className={isOpen ? "text-xs text-warn-foreground" : "text-xs text-muted-foreground"}>
            {isOpen
              ? t("governance.access.devicesDoorOpen", { port: door?.port ?? "" })
              : t("governance.access.devicesDoorClosed")}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isOpen ? (
            <Button size="sm" variant="outline" disabled={close.isPending} onClick={() => close.mutate()}>
              {t("governance.access.doorClose")}
            </Button>
          ) : (
            <Button size="sm" variant="outline" disabled={open.isPending} onClick={() => open.mutate()}>
              {t("governance.access.devicesDoorOpenAction")}
            </Button>
          )}
          <Button
            size="sm"
            disabled={!isOpen || pair.isPending}
            onClick={() => pair.mutate()}
          >
            {t("governance.access.devicesPair")}
          </Button>
        </div>
      </div>
      {!isOpen ? (
        <p className="px-4 pb-2 text-xs text-muted-foreground">{t("governance.access.devicesPairHint")}</p>
      ) : null}
      {code && isOpen ? (
        <div className="flex flex-wrap items-start gap-4 px-4 py-3" data-testid="pairing-code">
          <QrCode rows={code.qr} label={t("governance.access.devicesQrLabel")} />
          <div className="min-w-0 flex-1 space-y-1.5">
            <p className="text-xs text-muted-foreground">{t("governance.access.devicesCodeLead")}</p>
            <div className="font-mono text-xl tracking-widest text-foreground">{code.code}</div>
            <p className="text-xs text-muted-foreground">
              {t("governance.access.devicesCodeUntil", { when: when(code.expires_at, lang) })}
            </p>
            {code.urls.map((url) => (
              <div key={url} className="break-all font-mono text-xs text-foreground">
                {url}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <div className="flex items-center justify-between gap-4 px-4 pb-1 pt-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t("governance.access.devicesList", { n: devices.length })}
        </span>
        {devices.length > 0 ? (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => revokeAll.mutate()}>
            {t("governance.access.devicesRevokeAll")}
          </Button>
        ) : null}
      </div>
      {devices.length === 0 ? (
        <p className="px-4 py-3 text-xs text-muted-foreground">{t("governance.access.devicesNone")}</p>
      ) : (
        devices.map((device) => (
          <DeviceRow
            key={device.id}
            device={device}
            lang={lang}
            busy={busy}
            onRevoke={() => revokeOne.mutate(device.id)}
          />
        ))
      )}
      <p className="px-4 pt-2 text-xs text-muted-foreground">{t("governance.access.devicesNote")}</p>
      <p className="px-4 pb-3 pt-1 text-xs text-muted-foreground">{t("governance.access.devicesRemote")}</p>
    </>
  );
}
