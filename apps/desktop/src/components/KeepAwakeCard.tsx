import { useId } from "react";
import { useQuery } from "@tanstack/react-query";

import { Switch } from "@/components/ui/switch";
import { getKeepAwake } from "@/lib/api";
import { useT } from "@/lib/i18n";

const MODE_KEY = "CHIMERA_KEEP_AWAKE";
const BATTERY_KEY = "CHIMERA_KEEP_AWAKE_ON_BATTERY";
const MODES = ["off", "working", "always"] as const;
type Mode = (typeof MODES)[number];

function isMode(value: string): value is Mode {
  return (MODES as readonly string[]).includes(value);
}

/**
 * Whether the app keeps this computer from going to sleep while it has work
 * (`chimera/core/keep_awake.py`). Off by default: it changes what the machine does and spends
 * battery, and nothing measured says it should be on.
 *
 * The card says what it does NOT do as plainly as what it does — idle sleep only, so a closed lid
 * still sleeps — because "keep awake" invites the stronger reading, and a schedule that died with a
 * closed lid under a switch that promised otherwise is worse than no switch. When there is work and
 * the machine is not held (on battery, or a system with no mechanism), the keeper says so and so
 * does this card.
 */
export function KeepAwakeCard({
  mode,
  onBattery,
  onSave,
}: {
  mode: string;
  onBattery: boolean;
  onSave: (updates: Record<string, string>) => void;
}) {
  const t = useT();
  const headingId = useId();
  const current: Mode = isMode(mode) ? mode : "off";
  const live = useQuery({ queryKey: ["keep-awake"], queryFn: () => getKeepAwake(), refetchInterval: 10000 });
  const blocked = current === "off" ? "" : (live.data?.blocked ?? "");
  const labels: Record<Mode, [string, string]> = {
    off: [t("settings.keepAwake.off"), t("settings.keepAwake.offHint")],
    working: [t("settings.keepAwake.working"), t("settings.keepAwake.workingHint")],
    always: [t("settings.keepAwake.always"), t("settings.keepAwake.alwaysHint")],
  };
  const batteryLabel = t("settings.keepAwake.onBattery");

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.keepAwake")}
      </h2>
      <div className="divide-y divide-hairline">
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.keepAwake.intro")}</p>
        <fieldset className="space-y-2 px-4 py-3">
          <legend className="sr-only">{t("settings.card.keepAwake")}</legend>
          {MODES.map((value) => (
            <label key={value} className="flex cursor-pointer items-start gap-2">
              <input
                type="radio"
                name={`${headingId}-mode`}
                value={value}
                checked={current === value}
                onChange={() => onSave({ [MODE_KEY]: value })}
                className="mt-1"
              />
              <span className="min-w-0">
                <span className="block text-sm">{labels[value][0]}</span>
                <span className="block text-xs text-muted-foreground">{labels[value][1]}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="space-y-1 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm font-medium">{batteryLabel}</span>
            <Switch
              checked={onBattery}
              label={batteryLabel}
              disabled={current === "off"}
              onChange={(next) => onSave({ [BATTERY_KEY]: next ? "true" : "false" })}
            />
          </div>
          <div className="text-xs text-muted-foreground">{t("settings.keepAwake.onBatteryHint")}</div>
        </div>
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.keepAwake.limits")}</p>
        {blocked === "battery" && (
          <p role="status" className="px-4 py-2.5 text-xs text-warn-foreground">
            {t("settings.keepAwake.blockedBattery")}
          </p>
        )}
        {blocked === "unsupported" && (
          <p role="status" className="px-4 py-2.5 text-xs text-warn-foreground">
            {t("settings.keepAwake.blockedUnsupported")}
          </p>
        )}
      </div>
    </section>
  );
}
